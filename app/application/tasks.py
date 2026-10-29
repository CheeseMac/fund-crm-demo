"""待办任务用例：创建带权限校验与审计的任务（从 POST /tasks 路由抽出）。"""
from hashlib import sha256

from sqlalchemy.orm import Session

from app.dependencies import Actor, get_customer, get_contact
from app.models import FollowUp, AuditLog
from app.schemas import TaskIn


def create_task(db: Session, actor: Actor, payload: TaskIn) -> FollowUp:
    get_customer(db, actor, payload.customer_id)
    if payload.contact_id:
        get_contact(db, actor, payload.customer_id, payload.contact_id)
    task = FollowUp(tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                    customer_id=payload.customer_id, contact_id=payload.contact_id,
                    due_on=payload.due_on, cadence_days=payload.cadence_days,
                    reason_md=payload.reason_md)
    db.add(task)
    db.flush()
    db.add(AuditLog(
        tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
        action="create", resource_type="followup", resource_id=task.id,
        details_hash=sha256((payload.reason_md or "").encode()).hexdigest(),
    ))
    db.commit()
    return task
