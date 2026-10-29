"""Outbox：业务事务内写入事件，派发器以幂等方式消费（docs/05 §3/§4）。

失败事件保留 pending 并累计 attempts，超过上限标记 failed 供人工处理；
市场事件处理复用 services.market 的按用户匹配（内部已有 dedup_key 幂等）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker, Session

from app.models import OutboxEvent
from app.services.market import reconcile_market_signal

MAX_ATTEMPTS = 3


def emit(db: Session, event_type: str, tenant_id: str, actor_id: str | None,
         payload: dict, idempotency_key: str, schema_version: int = 1) -> OutboxEvent:
    """调用方在同一个业务事务中 commit；重复 key 视为已存在（幂等）。"""
    existing = db.scalar(select(OutboxEvent).where(
        OutboxEvent.idempotency_key == idempotency_key))
    if existing:
        return existing
    event = OutboxEvent(
        event_type=event_type, tenant_id=tenant_id, actor_id=actor_id,
        payload=payload, idempotency_key=idempotency_key,
        schema_version=schema_version,
    )
    db.add(event)
    return event


def _handle(event: OutboxEvent, factory: sessionmaker) -> int:
    if event.event_type == "market.signal.accepted.v1":
        signal_id = event.payload.get("signal_id", "")
        return reconcile_market_signal(factory, event.tenant_id, signal_id,
                                       event.actor_id or "")
    raise ValueError(f"未知事件类型: {event.event_type}")


def process_one(factory: sessionmaker, event_id: str) -> int:
    """执行单个事件并落处理状态；失败保留 pending 待重试。返回副作用创建条数。"""
    with factory() as db:
        event = db.get(OutboxEvent, event_id)
        if event is None or event.delivery_status == "processed":
            return 0
        try:
            created = _handle(event, factory)
            event.delivery_status = "processed"
            event.processed_at = datetime.now(timezone.utc)
            event.attempts += 1
            db.commit()
            return created
        except Exception:  # noqa: BLE001 - 记账后交给下一轮重试
            db.rollback()
            fresh = db.get(OutboxEvent, event_id)
            if fresh is not None:
                fresh.attempts += 1
                if fresh.attempts >= MAX_ATTEMPTS:
                    fresh.delivery_status = "failed"
                db.commit()
            return 0


def drain(factory: sessionmaker, limit: int = 50) -> int:
    """批量处理 pending 事件；返回成功处理条数（副作用幂等）。"""
    with factory() as db:
        ids = db.scalars(select(OutboxEvent.event_id).where(
            OutboxEvent.delivery_status == "pending",
        ).order_by(OutboxEvent.occurred_at.asc()).limit(limit)).all()
    processed = 0
    for event_id in ids:
        with factory() as db:
            event = db.get(OutboxEvent, event_id)
            done = event is not None and event.delivery_status == "processed"
        if done:
            continue
        # process_one 自带状态校验与失败记账
        process_one(factory, event_id)
        with factory() as db:
            event = db.get(OutboxEvent, event_id)
            if event is not None and event.delivery_status == "processed":
                processed += 1
    return processed
