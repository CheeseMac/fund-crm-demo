"""Assistant proposal-confirm-write transaction; the client must confirm all writes."""
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from hashlib import sha256
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.dependencies import Actor, get_customer, get_contact
from app.models import PendingAction, Visit, Need, Holding, FollowUp, AuditLog
from app.schemas import ResolvedAction
from app.services.parser import (
    fallback_parse, get_visible_targets, llm_parse,
    resolve_actions, is_due_query, due_query_end, local_today,
)
from app.config import settings

def utcnow():
    return datetime.now(timezone.utc)

def list_due(db: Session, actor: Actor, until: date | None = None) -> list[dict]:
    until = until or local_today()
    rows = db.scalars(select(FollowUp).where(
        FollowUp.tenant_id == actor.tenant_id, FollowUp.owner_user_id == actor.user_id,
        FollowUp.status == "open", FollowUp.due_on <= until,
    ).order_by(FollowUp.due_on.asc()).limit(200)).all()
    return [
        {"id": t.id, "customer_id": t.customer_id, "contact_id": t.contact_id,
         "due_on": t.due_on.isoformat(), "reason_md": t.reason_md,
         "cadence_days": t.cadence_days} for t in rows
    ]

def interpret(db: Session, actor: Actor, message: str) -> dict:
    message = message.strip()
    if is_due_query(message):
        until = due_query_end(message)
        return {"status": "answered", "intent": "query_due", "until": until.isoformat(),
                "items": list_due(db, actor, until)}
    customers, contacts = get_visible_targets(db, actor)
    source = "offline_rules"
    warnings = []
    drafts = None
    if settings.llm_enabled:
        try:
            drafts = llm_parse(message, customers, contacts)
            source = "llm_extraction"
        except Exception:  # model/network/schema failures do not cause silent writes
            warnings.append("模型解析失败，已切换为有限规则解析，请特别核对预览")
    if drafts is None:
        drafts, notes = fallback_parse(message, customers, contacts)
        warnings.extend(notes)
    if not drafts:
        return {
            "status": "needs_clarification", "warnings": warnings,
            "message": "没有提取到可执行操作；请明确拜访、需求、购买或跟进日期。",
            "examples": [
                "今天拜访了王敏，需求：了解红利策略，30天后回访",
                "华星资管持有中证红利ETF，金额50万元，标签：红利",
                "本周谁需要跟进",
            ],
        }
    if len(drafts) > 8:
        raise HTTPException(422, "单条消息最多八个操作")
    actions = resolve_actions(db, actor, drafts)
    record = PendingAction(
        tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
        payload={"actions": [a.model_dump(mode="json") for a in actions],
                 "source": source},
        expires_at=utcnow() + timedelta(minutes=30),
    )
    db.add(record)
    db.commit()
    return {
        "status": "confirmation_required", "pending_action_id": record.id,
        "expires_at": record.expires_at.isoformat(),
        "source": source, "warnings": warnings,
        "preview": [a.model_dump(mode="json") for a in actions],
        "notice": "这只是预览；调用确认接口前不会更改客户事实或跟进任务。",
    }

def confirm(db: Session, actor: Actor, action_id: str) -> dict:
    # A row lock serializes double confirmations on PostgreSQL.
    pending = db.scalar(select(PendingAction).where(
        PendingAction.id == action_id,
        PendingAction.tenant_id == actor.tenant_id,
        PendingAction.owner_user_id == actor.user_id,
    ).with_for_update())
    if pending is None:
        raise HTTPException(404, "确认单不存在")
    if pending.status == "executed":
        return {"status": "executed", "idempotent_replay": True,
                "items": pending.result.get("items", [])}
    if pending.status != "pending":
        raise HTTPException(409, "确认单状态不可执行")
    expiration = pending.expires_at
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=timezone.utc)  # SQLite stores naive time
    if expiration < utcnow():
        pending.status = "expired"
        db.commit()
        raise HTTPException(410, "确认单已过期，请重新提交")
    try:
        actions = [ResolvedAction.model_validate(x) for x in pending.payload["actions"]]
    except (ValidationError, KeyError, TypeError):
        raise HTTPException(422, "确认数据校验失败")
    output = []
    for action in actions:
        get_customer(db, actor, action.customer_id)
        if action.contact_id:
            get_contact(db, actor, action.customer_id, action.contact_id)
        common = dict(tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                      customer_id=action.customer_id)
        if action.kind == "log_visit":
            obj = Visit(**common, contact_id=action.contact_id,
                        occurred_on=action.occurred_on or local_today(),
                        channel="meeting", body_md=action.summary_md)
        elif action.kind == "record_need":
            obj = Need(**common, contact_id=action.contact_id,
                       category=action.category, description=action.description,
                       tags=action.tags, details_md=action.summary_md or "")
        elif action.kind == "record_holding":
            obj = Holding(**common, product_name=action.product_name,
                          fund_code=action.fund_code, amount_cny=action.amount_cny,
                          tags=action.tags, as_of=local_today(),
                          source="sales_reported_unverified", verified=False)
        elif action.kind == "create_followup":
            obj = FollowUp(**common, contact_id=action.contact_id,
                           due_on=action.due_on, cadence_days=action.cadence_days,
                           reason_md=action.summary_md or "")
        else:
            raise HTTPException(422, "非法操作类型")
        db.add(obj)
        db.flush()
        db.add(AuditLog(
            tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
            action="assistant_confirm", resource_type=action.kind, resource_id=obj.id,
            details_hash=sha256(str(action.model_dump(mode="json")).encode()).hexdigest(),
        ))
        output.append({"kind": action.kind, "id": obj.id})
    pending.status = "executed"
    pending.result = {"items": output}
    db.commit()
    return {"status": "executed", "idempotent_replay": False, "items": output}
