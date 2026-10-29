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
    TaskSchedule, JobRun, OutboxEvent, UserNotificationPrefs,
)
from app.schemas import (
    TenantRegistration, LoginIn, UserIn, CustomerIn, ContactIn,
    InterpretIn, MarketSignalIn, TaskIn, HoldingIn, PrefsIn,
    MediaInitiateIn, MeetingIn,
)
from app.security import hash_password, verify_password, create_token
from app.application import assistant as assistant_app
from app.application import tasks as tasks_app
from app.application import market as market_app
from app.application import notifications as notifications_app
from app.application import media as media_app
from app.application import meetings as meetings_app
from app.workers.scheduling import ensure_default_schedule, next_occurrence
from app.services.market import reconcile_due_tasks
from app.ai.text_rules import local_today
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
    return assistant_app.interpret(db, actor, payload.message)

@router.post("/assistant/confirm/{pending_id}")
def assistant_confirm(pending_id: str, actor: Actor = Depends(get_actor),
                      db: Session = Depends(get_db)):
    return assistant_app.confirm_actions(db, actor, pending_id)

@router.post("/tasks", status_code=201)
def create_task(payload: TaskIn, actor: Actor = Depends(get_actor),
                db: Session = Depends(get_db)):
    task = tasks_app.create_task(db, actor, payload)
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
    return assistant_app.list_due(db, actor, until)

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
def reminder_reconcile(lead_days: int | None = None,
                       actor: Actor = Depends(get_actor),
                       db: Session = Depends(get_db)):
    # lead_days 缺省时使用服务端保存的用户偏好（跨设备一致）
    return notifications_app.reconcile_for_user(db, actor, lead_days)

@router.post("/market/signals", status_code=201)
def publish_market_signal(payload: MarketSignalIn,
                          actor: Actor = Depends(require_admin),
                          db: Session = Depends(get_db)):
    return market_app.publish_signal(db, actor, payload)

@router.post("/market/signals/{signal_id}/reconcile")
def retry_market_reconcile(signal_id: str, actor: Actor = Depends(require_admin),
                           db: Session = Depends(get_db)):
    item = db.scalar(select(MarketSignal).where(
        MarketSignal.id == signal_id, MarketSignal.tenant_id == actor.tenant_id))
    if not item:
        raise HTTPException(404, "事件不存在")
    return market_app.match_signal(db, actor, signal_id)

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

# ── V2：通知偏好 / 定时计划 / 作业与 Outbox（docs/05 §9.1）──

@router.get("/api/v2/me/notification-preferences")
def get_my_prefs(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    prefs = notifications_app.get_or_create_prefs(db, actor)
    return {"lead_days": prefs.lead_days, "timezone": prefs.timezone,
            "channels": prefs.channels, "digest_mode": prefs.digest_mode,
            "updated_at": prefs.updated_at.isoformat() if prefs.updated_at else None}

@router.put("/api/v2/me/notification-preferences")
def put_my_prefs(payload: PrefsIn, actor: Actor = Depends(get_actor),
                 db: Session = Depends(get_db)):
    prefs = notifications_app.update_prefs(
        db, actor, lead_days=payload.lead_days,
        timezone_name=payload.timezone, channels=payload.channels)
    audit(db, actor, "update", "notification_prefs", prefs.id, f"lead_days={prefs.lead_days}")
    return {"lead_days": prefs.lead_days, "timezone": prefs.timezone,
            "channels": prefs.channels, "digest_mode": prefs.digest_mode}

@router.get("/api/v2/schedules")
def my_schedules(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    ensure_default_schedule(db, actor.tenant_id, actor.user_id)  # 懒创建每日提醒计划
    rows = db.scalars(select(TaskSchedule).where(
        TaskSchedule.tenant_id == actor.tenant_id,
        TaskSchedule.owner_user_id == actor.user_id,
    ).order_by(TaskSchedule.next_run_at.asc())).all()
    return [{"id": s.id, "kind": s.kind, "hour": s.hour, "minute": s.minute,
             "weekday": s.weekday, "timezone": s.timezone, "enabled": s.enabled,
             "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
             "misfire_policy": s.misfire_policy} for s in rows]

@router.post("/api/v2/schedules/{schedule_id}/pause")
def pause_schedule(schedule_id: str, actor: Actor = Depends(get_actor),
                   db: Session = Depends(get_db)):
    schedule = db.scalar(select(TaskSchedule).where(
        TaskSchedule.id == schedule_id, TaskSchedule.tenant_id == actor.tenant_id,
        TaskSchedule.owner_user_id == actor.user_id))
    if not schedule:
        raise HTTPException(404, "计划不存在")
    schedule.enabled = False
    audit(db, actor, "pause", "schedule", schedule.id, schedule.kind)
    db.commit()
    return {"id": schedule.id, "enabled": schedule.enabled}

@router.post("/api/v2/schedules/{schedule_id}/resume")
def resume_schedule(schedule_id: str, actor: Actor = Depends(get_actor),
                    db: Session = Depends(get_db)):
    schedule = db.scalar(select(TaskSchedule).where(
        TaskSchedule.id == schedule_id, TaskSchedule.tenant_id == actor.tenant_id,
        TaskSchedule.owner_user_id == actor.user_id))
    if not schedule:
        raise HTTPException(404, "计划不存在")
    schedule.enabled = True
    schedule.next_run_at = next_occurrence(
        schedule.hour, schedule.minute, schedule.weekday, schedule.timezone, utcnow())
    audit(db, actor, "resume", "schedule", schedule.id, schedule.kind)
    db.commit()
    return {"id": schedule.id, "enabled": schedule.enabled,
            "next_run_at": schedule.next_run_at.isoformat()}

@router.get("/api/v2/jobs/{job_id}")
def job_status(job_id: str, actor: Actor = Depends(get_actor),
               db: Session = Depends(get_db)):
    job = db.scalar(select(JobRun).where(
        JobRun.id == job_id, JobRun.tenant_id == actor.tenant_id))
    if not job:
        raise HTTPException(404, "作业不存在")
    return {"id": job.id, "job_type": job.job_type, "status": job.status,
            "attempts": job.attempts, "scheduled_for": job.scheduled_for.isoformat(),
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "last_error": job.last_error}

@router.get("/api/v2/jobs")
def recent_jobs(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(JobRun).where(
        JobRun.tenant_id == actor.tenant_id,
    ).order_by(JobRun.created_at.desc()).limit(30)).all()
    return [{"id": j.id, "job_type": j.job_type, "status": j.status,
             "attempts": j.attempts, "scheduled_for": j.scheduled_for.isoformat(),
             "last_error": j.last_error} for j in rows]

@router.get("/api/v2/outbox")
def recent_outbox(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(OutboxEvent).where(
        OutboxEvent.tenant_id == actor.tenant_id,
    ).order_by(OutboxEvent.occurred_at.desc()).limit(30)).all()
    return [{"event_id": e.event_id, "event_type": e.event_type,
             "delivery_status": e.delivery_status, "attempts": e.attempts,
             "occurred_at": e.occurred_at.isoformat()} for e in rows]

# ── V2 PR3/PR4：多媒体与会议（docs/05 §5）──

@router.post("/api/v2/media/uploads/initiate", status_code=201)
def media_initiate(payload: MediaInitiateIn, actor: Actor = Depends(get_actor),
                   db: Session = Depends(get_db)):
    return media_app.initiate_upload(
        db, actor, payload.customer_id, payload.filename,
        payload.content_type, payload.declared_size)

@router.put("/api/v2/media/{asset_id}/upload")
async def media_upload(asset_id: str, request: __import__("fastapi").Request,
                       actor: Actor = Depends(get_actor),
                       db: Session = Depends(get_db)):
    payload = await request.body()
    return media_app.store_upload(db, actor, asset_id, payload)

@router.post("/api/v2/media/{asset_id}/complete")
def media_complete(asset_id: str, actor: Actor = Depends(get_actor),
                   db: Session = Depends(get_db)):
    asset = media_app.get_asset(db, actor, asset_id)
    return {"asset_id": asset["id"], "state": asset["state"],
            "scan_result": asset["scan_result"]}

@router.get("/api/v2/media")
def media_list(customer_id: str, actor: Actor = Depends(get_actor),
               db: Session = Depends(get_db)):
    return media_app.list_assets(db, actor, customer_id)

@router.get("/api/v2/media/{asset_id}")
def media_get(asset_id: str, actor: Actor = Depends(get_actor),
              db: Session = Depends(get_db)):
    return media_app.get_asset(db, actor, asset_id)

@router.get("/api/v2/media/{asset_id}/access")
def media_access(asset_id: str, actor: Actor = Depends(get_actor),
                 db: Session = Depends(get_db)):
    return media_app.issue_access(db, actor, asset_id)

@router.get("/api/v2/media/{asset_id}/download")
def media_download(asset_id: str, token: str,
                   db: Session = Depends(get_db)):
    # 下载凭短时票据（浏览器可直接打开）；元数据/签发接口仍需登录
    asset = media_app.read_for_download(db, asset_id, token)
    from fastapi.responses import Response
    data = media_app.load_bytes(asset)
    return Response(content=data, media_type=asset.content_type,
                    headers={"Content-Disposition":
                             f'attachment; filename="{asset.filename.encode("utf-8").decode("latin-1", "ignore")}"',
                             "Cache-Control": "no-store"})

@router.post("/api/v2/meetings", status_code=201)
def meetings_create(payload: MeetingIn, actor: Actor = Depends(get_actor),
                    db: Session = Depends(get_db)):
    meeting = meetings_app.create_meeting(
        db, actor, payload.customer_id, payload.title, payload.occurred_on,
        payload.contact_id, payload.asset_ids)
    return {"id": meeting.id, "linked_assets": getattr(meeting, "linked_assets", 0)}

@router.get("/api/v2/meetings")
def meetings_list(customer_id: str, actor: Actor = Depends(get_actor),
                  db: Session = Depends(get_db)):
    return meetings_app.list_meetings(db, actor, customer_id)

@router.get("/api/v2/meetings/{meeting_id}")
def meetings_get(meeting_id: str, actor: Actor = Depends(get_actor),
                 db: Session = Depends(get_db)):
    return meetings_app.get_meeting(db, actor, meeting_id)

@router.post("/api/v2/meetings/{meeting_id}/extract-draft")
def meetings_extract(meeting_id: str, actor: Actor = Depends(get_actor),
                     db: Session = Depends(get_db)):
    result = meetings_app.extract_draft_placeholder(db, actor, meeting_id)
    raise HTTPException(501, result["message"])
