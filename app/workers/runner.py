"""Job Runner：领取 queued/租约过期的 JobRun 并执行（docs/05 §4）。

领取即设置 lease_until + worker_id；副作用必须幂等（提醒按 dedup_key 去重）。
daily_reminder 按用户服务端偏好 lead_days 决定提前提醒口径。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, or_, and_
from sqlalchemy.orm import sessionmaker

from app.ai.text_rules import local_today
from app.dependencies import Actor
from app.models import JobRun, UserNotificationPrefs
from app.services.market import reconcile_due_tasks, reconcile_market_signal

LEASE_MINUTES = 5
MAX_ATTEMPTS = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def claim_jobs(factory: sessionmaker, limit: int = 10) -> list[str]:
    now = _utcnow()
    with factory() as db:
        rows = db.scalars(select(JobRun).where(or_(
            JobRun.status == "queued",
            and_(JobRun.status == "running",
                 JobRun.lease_until != None,  # noqa: E711
                 JobRun.lease_until < now.replace(tzinfo=None)),
        )).order_by(JobRun.available_at.asc()).limit(limit)).all()
        worker_id = uuid.uuid4().hex[:12]
        claimed: list[str] = []
        for job in rows:
            if job.status == "queued" and job.attempts >= MAX_ATTEMPTS:
                job.status = "dead"
                job.last_error = "超过最大重试次数"
                continue
            job.status = "running"
            job.worker_id = worker_id
            job.attempts += 1
            job.lease_until = (now + timedelta(minutes=LEASE_MINUTES)).replace(tzinfo=None)
            claimed.append(job.id)
        db.commit()
    return claimed


def _run_daily_reminder(factory: sessionmaker, job: JobRun) -> dict:
    """按用户偏好的 lead_days 生成到期提醒；dedup_key 保证幂等。"""
    with factory() as db:
        prefs = db.scalar(select(UserNotificationPrefs).where(
            UserNotificationPrefs.tenant_id == job.tenant_id,
            UserNotificationPrefs.user_id == job.owner_user_id,
        ))
        lead = prefs.lead_days if prefs else 0
        until = local_today() + timedelta(days=lead) if lead > 0 else None
    with factory() as db:
        actor = Actor(user_id=job.owner_user_id, tenant_id=job.tenant_id, role="sales")
        created = reconcile_due_tasks(db, actor, until)
    return {"alerts_created": created, "lead_days": lead}


def _run_market_match(factory: sessionmaker, job: JobRun) -> dict:
    signal_id = (job.payload or {}).get("signal_id", "")
    created = reconcile_market_signal(factory, job.tenant_id, signal_id,
                                      job.owner_user_id or "")
    return {"alerts_created": created}


def execute_job(factory: sessionmaker, job_id: str) -> dict:
    with factory() as db:
        job = db.get(JobRun, job_id)
        if job is None:
            return {"status": "missing"}
        if job.status in ("succeeded", "dead"):
            return {"status": job.status, "idempotent_replay": True}
        try:
            if job.job_type == "daily_reminder":
                detail = _run_daily_reminder(factory, job)
            elif job.job_type == "market_match":
                detail = _run_market_match(factory, job)
            else:
                raise ValueError(f"未知作业类型: {job.job_type}")
            job.status = "succeeded"
            job.finished_at = _utcnow().replace(tzinfo=None)
            job.last_error = None
            db.commit()
            return {"status": "succeeded", **detail}
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            with factory() as db2:
                fresh = db2.get(JobRun, job_id)
                if fresh is not None:
                    fresh.status = "queued" if fresh.attempts < MAX_ATTEMPTS else "dead"
                    fresh.last_error = str(exc)[:500]
                    db2.commit()
            raise


def run_due_jobs(factory: sessionmaker, limit: int = 10) -> list[dict]:
    results = []
    for job_id in claim_jobs(factory, limit):
        try:
            results.append({"id": job_id, **execute_job(factory, job_id)})
        except Exception as exc:  # noqa: BLE001
            results.append({"id": job_id, "status": "failed", "error": str(exc)[:200]})
    return results
