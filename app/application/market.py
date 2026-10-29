"""市场事件用例：管理员发布 -> 同事务写 Outbox -> 幂等匹配客户。

V1 响应形状保持不变（id / alerts_created / notice）：
HTTP 请求内尽力同步完成一次匹配（演示友好），失败不阻塞发布——
事件保持 pending，由 Scheduler 循环重试（至少一次 + 幂等）。
"""
from __future__ import annotations

from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.dependencies import Actor
from app.models import MarketSignal, AuditLog
from app.schemas import MarketSignalIn
from app.services.market import reconcile_market_signal
from app.workers.outbox import emit, process_one


def publish_signal(db: Session, actor: Actor, payload: MarketSignalIn) -> dict:
    existing = db.scalar(select(MarketSignal).where(
        MarketSignal.tenant_id == actor.tenant_id,
        MarketSignal.external_key == payload.external_key,
    ))
    if existing:
        raise HTTPException(409, "该外部事件标识已入库；可使用 reconcile 接口重试")
    signal = MarketSignal(tenant_id=actor.tenant_id, **payload.model_dump())
    db.add(signal)
    db.flush()
    db.add(AuditLog(tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                    action="publish", resource_type="market_signal",
                    resource_id=signal.id,
                    details_hash=sha256(payload.headline.encode("utf-8")).hexdigest()))
    event = emit(
        db, event_type="market.signal.accepted.v1",
        tenant_id=actor.tenant_id, actor_id=actor.user_id,
        payload={"signal_id": signal.id},
        idempotency_key=f"signal:{actor.tenant_id}:{signal.id}",
    )
    db.commit()  # 信号与 Outbox 事件同事务提交（docs/05 §4）
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False, autoflush=False)
    created = process_one(factory, event.event_id)
    return {"id": signal.id, "alerts_created": created,
            "notice": "人工事件匹配：仅用于内部复核，不是交易建议"}


def match_signal(db: Session, actor: Actor, signal_id: str) -> dict:
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False, autoflush=False)
    created = reconcile_market_signal(factory, actor.tenant_id, signal_id,
                                      actor.user_id)
    return {"alerts_created": created, "idempotent": True}
