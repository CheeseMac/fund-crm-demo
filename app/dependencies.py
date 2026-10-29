from dataclasses import dataclass
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Customer, Contact
from app.security import decode_token

bearer = HTTPBearer(auto_error=False)

@dataclass(frozen=True)
class Actor:
    user_id: str
    tenant_id: str
    role: str

def get_actor(
    token: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Actor:
    if token is None or token.scheme.lower() != "bearer":
        raise HTTPException(401, "请先登录")
    uid, tid = decode_token(token.credentials)
    # Verified JWT determines the transaction-local RLS context, not a client-supplied header.
    db.info.update(tenant_id=tid, user_id=uid)
    user = db.scalar(select(User).where(User.id == uid, User.tenant_id == tid))
    if user is None or not user.is_active:
        raise HTTPException(401, "账号不可用")
    return Actor(user_id=uid, tenant_id=tid, role=user.role)

def require_admin(actor: Actor = Depends(get_actor)) -> Actor:
    if actor.role != "admin":
        raise HTTPException(403, "需要租户管理员权限")
    return actor

def get_customer(db: Session, actor: Actor, customer_id: str) -> Customer:
    customer = db.scalar(select(Customer).where(
        Customer.id == customer_id, Customer.tenant_id == actor.tenant_id,
        Customer.owner_user_id == actor.user_id,
    ))
    if customer is None:
        raise HTTPException(404, "找不到可访问的客户")
    return customer

def get_contact(db: Session, actor: Actor, customer_id: str, contact_id: str) -> Contact:
    contact = db.scalar(select(Contact).where(
        Contact.id == contact_id, Contact.customer_id == customer_id,
        Contact.tenant_id == actor.tenant_id, Contact.owner_user_id == actor.user_id,
    ))
    if contact is None:
        raise HTTPException(404, "找不到当前客户的联系人")
    return contact
