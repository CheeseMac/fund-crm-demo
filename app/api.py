from datetime import datetime, timedelta, timezone
from hashlib import sha256
from decimal import Decimal
from sqlalchemy import select, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.database import get_db
from app.dependencies import Actor, get_actor, require_admin, get_customer, get_contact
from app.models import (
    Tenant, User, Customer, Contact, Visit, Need, Holding, FollowUp,
    MarketSignal, Alert, AuditLog, uid, utcnow,
)
from app.schemas import (
    TenantRegistration, LoginIn, UserIn, CustomerIn, ContactIn,
    InterpretIn, MarketSignalIn, TaskIn, HoldingIn,
)
from app.security import hash_password, verify_password, create_token
from app.services.assistant import interpret, confirm, list_due
from app.services.market import reconcile_market_signal, reconcile_due_tasks
from app.services.parser import local_today
from app.patch_schemas import CustomerPatch, NeedPatch

router = APIRouter()

def audit(db: Session, actor: Actor, action: str, kind: str, obj_id: str, content: str):
    db.add(AuditLog(
        tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
        action=action, resource_type=kind, resource_id=obj_id,
        details_hash=sha256(content.encode("utf-8")).hexdigest(),
    ))

@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}

@router.post("/auth/dev-register-tenant", status_code=201)
def dev_register_tenant(payload: TenantRegistration, db: Session = Depends(get_db)):
    if not settings.dev_mode or not settings.allow_dev_tenant_registration:
        raise HTTPException(404, "开发租户注册已关闭")
    # Generate authenticated-looking context only for restricted local DEV bootstrap.
    tid, uid_value = uid(), uid()
    db.info.update(tenant_id=tid, user_id=uid_value)
    if db.scalar(select(Tenant.id).where(Tenant.slug == payload.tenant_slug)):
        raise HTTPException(409, "租户标识已存在")
    tenant = Tenant(id=tid, slug=payload.tenant_slug, name=payload.tenant_name)
    user = User(id=uid_value, tenant_id=tid,
                email=payload.email.strip().lower(),
                password_hash=hash_password(payload.password), role="admin")
    db.add_all([tenant, user])
    db.commit()
    return {"tenant_id": tid, "user_id": uid_value,
            "access_token": create_token(uid_value, tid), "token_type": "bearer"}

@router.post("/auth/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    tenant_id = db.scalar(select(Tenant.id).where(Tenant.slug == payload.tenant_slug))
    if tenant_id is None:
        raise HTTPException(401, "租户、账号或密码错误")
    # Public lookup exposes only tenant ID and slug; no tenant customer data.
    db.rollback()
    db.info.update(tenant_id=tenant_id, user_id="login")
    user = db.scalar(select(User).where(
        User.tenant_id == tenant_id, User.email == payload.email.strip().lower(),
    ))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "租户、账号或密码错误")
    return {"access_token": create_token(user.id, tenant_id), "token_type": "bearer"}

@router.get("/auth/me")
def me(actor: Actor = Depends(get_actor)):
    return {"user_id": actor.user_id, "tenant_id": actor.tenant_id, "role": actor.role}

@router.post("/users", status_code=201)
def create_user(
    payload: UserIn, actor: Actor = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()
    if db.scalar(select(User.id).where(
        User.tenant_id == actor.tenant_id, User.email == email
    )):
        raise HTTPException(409, "用户已存在")
    user = User(tenant_id=actor.tenant_id, email=email,
                password_hash=hash_password(payload.password), role=payload.role)
    db.add(user)
    db.commit()
    return {"id": user.id, "email": user.email, "role": user.role}

@router.post("/customers", status_code=201)
def create_customer(payload: CustomerIn, actor: Actor = Depends(get_actor),
                    db: Session = Depends(get_db)):
    customer = Customer(tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                        name=payload.name.strip(), customer_type=payload.customer_type,
                        tags=payload.tags, background_md=payload.background_md)
    try:
        db.add(customer)
        db.flush()
        audit(db, actor, "create", "customer", customer.id, payload.name)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "你的客户列表已存在同名客户")
    return {"id": customer.id, "name": customer.name, "customer_type": customer.customer_type}

@router.get("/customers")
def list_customers(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    rows = db.scalars(select(Customer).where(
        Customer.tenant_id == actor.tenant_id, Customer.owner_user_id == actor.user_id,
    ).order_by(Customer.created_at.desc()).limit(500)).all()
    return [{"id": c.id, "name": c.name, "type": c.customer_type,
             "tags": c.tags} for c in rows]

@router.get("/customers/{customer_id}")
def customer_detail(customer_id: str, actor: Actor = Depends(get_actor),
                    db: Session = Depends(get_db)):
    c = get_customer(db, actor, customer_id)
    return {"id": c.id, "name": c.name, "type": c.customer_type,
            "tags": c.tags, "background_md": c.background_md}

@router.patch("/customers/{customer_id}")
def update_customer(customer_id: str, payload: CustomerPatch,
                    actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    c = get_customer(db, actor, customer_id)
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(422, "没有需要更新的字段")
    for key, value in fields.items():
        if value is not None:
            setattr(c, key, value)
    audit(db, actor, "update", "customer", c.id, ",".join(sorted(fields)))
    db.commit()
    return {"id": c.id, "tags": c.tags, "background_md": c.background_md}

@router.patch("/needs/{need_id}")
def update_need(need_id: str, payload: NeedPatch,
                actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    need = db.scalar(select(Need).where(
        Need.id == need_id, Need.tenant_id == actor.tenant_id,
        Need.owner_user_id == actor.user_id,
    ).with_for_update())
    if not need:
        raise HTTPException(404, "需求不存在")
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(422, "没有需要更新的字段")
    for key, value in fields.items():
        if value is not None:
            setattr(need, key, value)
    audit(db, actor, "update", "need", need.id, ",".join(sorted(fields)))
    db.commit()
    return {"id": need.id, "status": need.status, "description": need.description,
            "tags": need.tags}

@router.post("/customers/{customer_id}/contacts", status_code=201)
def create_contact(customer_id: str, payload: ContactIn,
                   actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    get_customer(db, actor, customer_id)
    contact = Contact(tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                      customer_id=customer_id, name=payload.name.strip(),
                      title=payload.title, notes_md=payload.notes_md)
    try:
        db.add(contact)
        db.flush()
        audit(db, actor, "create", "contact", contact.id, payload.name)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该客户已存在同名联系人")
    return {"id": contact.id, "customer_id": contact.customer_id,
            "name": contact.name, "title": contact.title}

@router.get("/customers/{customer_id}/contacts")
def list_contacts(customer_id: str, actor: Actor = Depends(get_actor),
                  db: Session = Depends(get_db)):
    get_customer(db, actor, customer_id)
    contacts = db.scalars(select(Contact).where(
        Contact.tenant_id == actor.tenant_id,
        Contact.owner_user_id == actor.user_id,
        Contact.customer_id == customer_id,
    )).all()
    return [{"id": c.id, "name": c.name, "title": c.title} for c in contacts]

@router.post("/customers/{customer_id}/holdings", status_code=201)
def create_holding(customer_id: str, payload: HoldingIn,
                   actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    get_customer(db, actor, customer_id)
    h = Holding(tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                customer_id=customer_id, product_name=payload.product_name,
                fund_code=payload.fund_code, amount_cny=payload.amount_cny,
                as_of=payload.as_of, tags=payload.tags,
                source="sales_reported_unverified", verified=False,
                note_md=payload.note_md)
    db.add(h)
    db.flush()
    audit(db, actor, "create_unverified", "holding", h.id, payload.product_name)
    db.commit()
    return {"id": h.id, "verified": False, "source": h.source}

@router.get("/customers/{customer_id}/holdings")
def list_holdings(customer_id: str, actor: Actor = Depends(get_actor),
                  db: Session = Depends(get_db)):
    get_customer(db, actor, customer_id)
    rows = db.scalars(select(Holding).where(
        Holding.tenant_id == actor.tenant_id,
        Holding.owner_user_id == actor.user_id, Holding.customer_id == customer_id,
    ).order_by(Holding.as_of.desc()).limit(100)).all()
    return [{"id": h.id, "product_name": h.product_name, "fund_code": h.fund_code,
             "amount_cny": str(h.amount_cny) if h.amount_cny is not None else None,
             "as_of": h.as_of.isoformat(), "tags": h.tags, "source": h.source,
             "verified": h.verified} for h in rows]

@router.get("/customers/{customer_id}/visits")
def list_visits(customer_id: str, actor: Actor = Depends(get_actor),
                db: Session = Depends(get_db)):
    get_customer(db, actor, customer_id)
    rows = db.scalars(select(Visit).where(
        Visit.tenant_id == actor.tenant_id, Visit.owner_user_id == actor.user_id,
        Visit.customer_id == customer_id,
    ).order_by(Visit.occurred_on.desc()).limit(100)).all()
    return [{"id": v.id, "contact_id": v.contact_id,
             "occurred_on": v.occurred_on.isoformat(), "body_md": v.body_md}
            for v in rows]

@router.get("/customers/{customer_id}/needs")
def list_needs(customer_id: str, actor: Actor = Depends(get_actor),
               db: Session = Depends(get_db)):
    get_customer(db, actor, customer_id)
    rows = db.scalars(select(Need).where(
        Need.tenant_id == actor.tenant_id, Need.owner_user_id == actor.user_id,
        Need.customer_id == customer_id,
    ).order_by(Need.created_at.desc()).limit(100)).all()
    return [{"id": x.id, "contact_id": x.contact_id, "description": x.description,
             "status": x.status, "category": x.category, "tags": x.tags}
            for x in rows]

@router.get("/customers/{customer_id}/export.md")
def export_customer_notes(customer_id: str, actor: Actor = Depends(get_actor),
                          db: Session = Depends(get_db)):
    c = get_customer(db, actor, customer_id)
    visits = db.scalars(select(Visit).where(
        Visit.tenant_id == actor.tenant_id, Visit.owner_user_id == actor.user_id,
        Visit.customer_id == customer_id,
    ).order_by(Visit.occurred_on.desc()).limit(1000)).all()
    needs = db.scalars(select(Need).where(
        Need.tenant_id == actor.tenant_id, Need.owner_user_id == actor.user_id,
        Need.customer_id == customer_id,
    )).all()
    lines = [f"# 客户档案：{c.name}", "", "> 内部机密；禁止对外转发。", "", "## 客户背景",
             "", c.background_md or "（未填写）", "", "## 需求台账", ""]
    lines.extend(f"- [{n.status}] {n.description}" for n in needs)
    lines.extend(["", "## 拜访记录", ""])
    for visit in visits:
        lines.extend([f"### {visit.occurred_on.isoformat()}", "", visit.body_md, ""])
    return PlainTextResponse("\n".join(lines), media_type="text/markdown",
                             headers={"Content-Disposition": "attachment; filename=customer-notes.md",
                                      "Cache-Control": "no-store"})

@router.post("/assistant/interpret")
def assistant_interpret(payload: InterpretIn, actor: Actor = Depends(get_actor),
                        db: Session = Depends(get_db)):
    return interpret(db, actor, payload.message)

@router.post("/assistant/confirm/{pending_id}")
def assistant_confirm(pending_id: str, actor: Actor = Depends(get_actor),
                      db: Session = Depends(get_db)):
    return confirm(db, actor, pending_id)

@router.post("/tasks", status_code=201)
def create_task(payload: TaskIn, actor: Actor = Depends(get_actor),
                db: Session = Depends(get_db)):
    get_customer(db, actor, payload.customer_id)
    if payload.contact_id:
        get_contact(db, actor, payload.customer_id, payload.contact_id)
    task = FollowUp(tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                    customer_id=payload.customer_id, contact_id=payload.contact_id,
                    due_on=payload.due_on, cadence_days=payload.cadence_days,
                    reason_md=payload.reason_md)
    db.add(task)
    db.flush()
    audit(db, actor, "create", "followup", task.id, payload.reason_md)
    db.commit()
    return {"id": task.id, "due_on": task.due_on.isoformat()}

@router.get("/tasks")
def list_tasks(status: str = "open", actor: Actor = Depends(get_actor),
               db: Session = Depends(get_db)):
    q = select(FollowUp).where(
        FollowUp.tenant_id == actor.tenant_id,
        FollowUp.owner_user_id == actor.user_id,
    )
    if status != "all":
        q = q.where(FollowUp.status == status)
    rows = db.scalars(q.order_by(FollowUp.due_on.asc()).limit(300)).all()
    return [{"id": t.id, "customer_id": t.customer_id, "contact_id": t.contact_id,
             "due_on": t.due_on.isoformat(), "status": t.status,
             "cadence_days": t.cadence_days, "reason_md": t.reason_md} for t in rows]

@router.get("/tasks/due")
def due_tasks(until: __import__("datetime").date | None = None,
              actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    return list_due(db, actor, until)

@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: str, actor: Actor = Depends(get_actor),
                  db: Session = Depends(get_db)):
    task = db.scalar(select(FollowUp).where(
        FollowUp.id == task_id, FollowUp.tenant_id == actor.tenant_id,
        FollowUp.owner_user_id == actor.user_id,
    ).with_for_update())
    if task is None:
        raise HTTPException(404, "任务不存在")
    if task.status == "done":
        return {"status": "done", "next_task_id": None, "already_completed": True}
    task.status = "done"
    task.completed_at = utcnow()
    next_task = None
    if task.cadence_days:
        next_task = FollowUp(
            tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
            customer_id=task.customer_id, contact_id=task.contact_id,
            due_on=max(local_today(), task.due_on) + timedelta(days=task.cadence_days),
            cadence_days=task.cadence_days, reason_md=task.reason_md,
        )
        db.add(next_task)
        db.flush()
    audit(db, actor, "complete", "followup", task.id, task.due_on.isoformat())
    db.commit()
    return {"status": "done", "next_task_id": next_task.id if next_task else None,
            "already_completed": False}

@router.post("/reminders/reconcile")
def reminder_reconcile(lead_days: int = 0, actor: Actor = Depends(get_actor),
                       db: Session = Depends(get_db)):
    until = local_today() + timedelta(days=lead_days) if lead_days > 0 else None
    return {"alerts_created": reconcile_due_tasks(db, actor, until), "lead_days": lead_days}

@router.post("/market/signals", status_code=201)
def publish_market_signal(payload: MarketSignalIn,
                          actor: Actor = Depends(require_admin),
                          db: Session = Depends(get_db)):
    existing = db.scalar(select(MarketSignal).where(
        MarketSignal.tenant_id == actor.tenant_id,
        MarketSignal.external_key == payload.external_key,
    ))
    if existing:
        raise HTTPException(409, "该外部事件标识已入库；可使用 reconcile 接口重试")
    signal = MarketSignal(tenant_id=actor.tenant_id, **payload.model_dump())
    db.add(signal)
    db.flush()
    audit(db, actor, "publish", "market_signal", signal.id, payload.headline)
    db.commit()
    # Post-commit reconciliation. If it fails, admin can retry the idempotent endpoint.
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False, autoflush=False)
    created = reconcile_market_signal(factory, actor.tenant_id, signal.id, actor.user_id)
    return {"id": signal.id, "alerts_created": created,
            "notice": "人工事件匹配：仅用于内部复核，不是交易建议"}

@router.post("/market/signals/{signal_id}/reconcile")
def retry_market_reconcile(signal_id: str, actor: Actor = Depends(require_admin),
                           db: Session = Depends(get_db)):
    item = db.scalar(select(MarketSignal).where(
        MarketSignal.id == signal_id, MarketSignal.tenant_id == actor.tenant_id))
    if not item:
        raise HTTPException(404, "事件不存在")
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False, autoflush=False)
    created = reconcile_market_signal(factory, actor.tenant_id, signal_id, actor.user_id)
    return {"alerts_created": created, "idempotent": True}

@router.get("/alerts")
def list_alerts(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    rows = db.scalars(select(Alert).where(
        Alert.tenant_id == actor.tenant_id,
        Alert.owner_user_id == actor.user_id,
    ).order_by(Alert.created_at.desc()).limit(200)).all()
    return [{"id": a.id, "customer_id": a.customer_id, "kind": a.kind,
             "signal_id": a.signal_id, "task_id": a.task_id,
             "status": a.status, "reason_md": a.reason_md} for a in rows]

@router.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: str, actor: Actor = Depends(get_actor),
              db: Session = Depends(get_db)):
    alert = db.scalar(select(Alert).where(
        Alert.id == alert_id, Alert.tenant_id == actor.tenant_id,
        Alert.owner_user_id == actor.user_id,
    ))
    if not alert:
        raise HTTPException(404, "提醒不存在")
    alert.status = "acknowledged"
    audit(db, actor, "ack", "alert", alert.id, alert.dedup_key)
    db.commit()
    return {"status": alert.status}
