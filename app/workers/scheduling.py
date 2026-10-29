"""Scheduler：把持久化计划物化为 JobRun（幂等），并推进 next_run_at。

可靠性（docs/05 §4）：
- UNIQUE(schedule_id, scheduled_for) 防止同一逻辑时点重复物化；
- misfire_policy=run_once 时停机错过多个周期只补跑一次；skip 则直接跳到未来；
- Scheduler 只读写计划与作业元数据，业务查询在 Worker 内按 owner scope 执行。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import TaskSchedule, JobRun, uid

MISFIRE_GRACE_DAYS = 1  # 超过一个周期未运行视为过期


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def next_occurrence(hour: int, minute: int, weekday: int | None,
                    tz_name: str, after_utc: datetime) -> datetime:
    """计划在本地时区的下一次墙钟时刻（严格晚于 after_utc），返回 UTC。"""
    tz = ZoneInfo(tz_name)
    local = after_utc.astimezone(tz)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if weekday is None:  # 每天
        if candidate <= local:
            candidate += timedelta(days=1)
    else:  # 每周（0=周一）
        delta = (weekday - candidate.weekday()) % 7
        candidate += timedelta(days=delta)
        if candidate <= local:
            candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def ensure_default_schedule(db: Session, tenant_id: str, user_id: str,
                            kind: str = "daily_reminder") -> TaskSchedule:
    """为用户确保存在默认计划（每日 08:30 生成到期提醒）。"""
    existing = db.scalar(select(TaskSchedule).where(
        TaskSchedule.tenant_id == tenant_id,
        TaskSchedule.owner_user_id == user_id,
        TaskSchedule.kind == kind,
    ))
    if existing:
        db.commit()
        return existing
    schedule = TaskSchedule(
        id=uid(), tenant_id=tenant_id, owner_user_id=user_id, kind=kind,
        hour=8, minute=30, weekday=None, timezone=settings.app_timezone,
        next_run_at=next_occurrence(8, 30, None, settings.app_timezone, _utcnow()),
        enabled=True, misfire_policy="run_once",
    )
    db.add(schedule)
    try:
        db.commit()
    except IntegrityError:  # 并发下已被他人创建
        db.rollback()
        existing = db.scalar(select(TaskSchedule).where(
            TaskSchedule.tenant_id == tenant_id,
            TaskSchedule.owner_user_id == user_id,
            TaskSchedule.kind == kind,
        ))
        return existing
    return schedule


def materialize_due_schedules(db: Session, now_utc: datetime | None = None) -> int:
    now = now_utc or _utcnow()
    schedules = db.scalars(select(TaskSchedule).where(
        TaskSchedule.enabled.is_(True), TaskSchedule.next_run_at <= now,
    )).all()
    created = 0
    for schedule in schedules:
        due_at = schedule.next_run_at
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)  # SQLite 存 naive（UTC）
        stale_days = (now - due_at).days
        if schedule.misfire_policy == "skip" and stale_days >= MISFIRE_GRACE_DAYS:
            schedule.next_run_at = next_occurrence(
                schedule.hour, schedule.minute, schedule.weekday,
                schedule.timezone, now)
            db.commit()
            continue
        due_at = schedule.next_run_at
        naive = due_at.replace(tzinfo=None)
        job = JobRun(
            id=uid(), schedule_id=schedule.id,
            tenant_id=schedule.tenant_id, owner_user_id=schedule.owner_user_id,
            job_type=schedule.kind, payload={},
            scheduled_for=naive, available_at=naive, status="queued",
            idempotency_key=f"schedule:{schedule.id}:{naive.strftime('%Y%m%dT%H%M%S')}",
        )
        db.add(job)
        try:
            db.flush()
            created += 1
        except IntegrityError:  # 该逻辑时点已物化（如重复 tick）
            db.rollback()
            job = None
        # 无论本次是否新建，next_run_at 都推进到严格晚于 now 的下一时点
        schedule.next_run_at = next_occurrence(
            schedule.hour, schedule.minute, schedule.weekday, schedule.timezone, now)
        db.commit()
    return created
