"""通知偏好用例：服务端持久化的提醒设置 + 按偏好同步到期提醒。

替代浏览器 localStorage 方案（docs/05 §1 关键缺口）：
提前天数跨设备一致，Scheduler 的 daily_reminder 作业与"立即同步"共用同一口径。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.text_rules import local_today
from app.dependencies import Actor
from app.models import UserNotificationPrefs
from app.services.market import reconcile_due_tasks


def get_or_create_prefs(db: Session, actor: Actor) -> UserNotificationPrefs:
    prefs = db.scalar(select(UserNotificationPrefs).where(
        UserNotificationPrefs.tenant_id == actor.tenant_id,
        UserNotificationPrefs.user_id == actor.user_id,
    ))
    if prefs is None:
        prefs = UserNotificationPrefs(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
            channels=["inbox"], digest_mode="instant",
        )
        db.add(prefs)
        db.commit()
    return prefs


def update_prefs(db: Session, actor: Actor, lead_days: int | None = None,
                 timezone_name: str | None = None,
                 channels: list[str] | None = None) -> UserNotificationPrefs:
    prefs = get_or_create_prefs(db, actor)
    if lead_days is not None:
        prefs.lead_days = max(0, min(30, int(lead_days)))
    if timezone_name:
        prefs.timezone = timezone_name
    if channels is not None:
        prefs.channels = channels
    prefs.updated_at = datetime.now(timezone.utc)
    db.commit()
    return prefs


def reconcile_for_user(db: Session, actor: Actor, lead_days: int | None = None) -> dict:
    """lead_days 缺省时使用服务端偏好；显式传入时以传入为准（V1 兼容）。"""
    prefs = get_or_create_prefs(db, actor)
    effective = prefs.lead_days if lead_days is None else max(0, lead_days)
    until = local_today() + timedelta(days=effective) if effective > 0 else None
    created = reconcile_due_tasks(db, actor, until)
    return {"alerts_created": created, "lead_days": effective}
