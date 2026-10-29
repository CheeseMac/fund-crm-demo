from datetime import datetime, timezone, date
from uuid import uuid4
from decimal import Decimal
from sqlalchemy import (
    String, Text, Integer, Boolean, Date, DateTime, Numeric, ForeignKey,
    ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Index, JSON
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

def uid() -> str:
    return str(uuid4())

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email"),
        UniqueConstraint("tenant_id", "id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="sales", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", "id"),
        UniqueConstraint("tenant_id", "owner_user_id", "name"),
        ForeignKeyConstraint(["tenant_id", "owner_user_id"], ["users.tenant_id", "users.id"]),
        CheckConstraint("customer_type IN ('individual','institution')", name="ck_customer_type"),
        Index("ix_customer_owner_name", "tenant_id", "owner_user_id", "name"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_type: Mapped[str] = mapped_column(String(20), nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    background_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", "customer_id", "id"),
        UniqueConstraint("tenant_id", "owner_user_id", "customer_id", "name"),
        ForeignKeyConstraint(
            ["tenant_id", "owner_user_id", "customer_id"],
            ["customers.tenant_id", "customers.owner_user_id", "customers.id"],
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    # 联系方式属于个人信息，真实部署须字段加密/脱敏/审计，示例不收集
    notes_md: Mapped[str] = mapped_column(Text, default="", nullable=False)

class OwnedCustomerRecord:
    """Mixin: composite FK forbids cross-tenant AND cross-owner customer references."""
    @classmethod
    def __declare_last__(cls):
        pass

customer_fk = ForeignKeyConstraint(
    ["tenant_id", "owner_user_id", "customer_id"],
    ["customers.tenant_id", "customers.owner_user_id", "customers.id"],
)
contact_fk = ForeignKeyConstraint(
    ["tenant_id", "owner_user_id", "customer_id", "contact_id"],
    ["contacts.tenant_id", "contacts.owner_user_id", "contacts.customer_id", "contacts.id"],
)

class Visit(Base):
    __tablename__ = "visits"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "customer_id"],
                             ["customers.tenant_id", "customers.owner_user_id", "customers.id"]),
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "customer_id", "contact_id"],
                             ["contacts.tenant_id", "contacts.owner_user_id", "contacts.customer_id", "contacts.id"]),
        Index("ix_visit_recent", "tenant_id", "owner_user_id", "occurred_on"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    contact_id: Mapped[str | None] = mapped_column(String(36))
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(String(30), default="meeting", nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Need(Base):
    __tablename__ = "needs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "customer_id"],
                             ["customers.tenant_id", "customers.owner_user_id", "customers.id"]),
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "customer_id", "contact_id"],
                             ["contacts.tenant_id", "contacts.owner_user_id", "contacts.customer_id", "contacts.id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    contact_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="open")
    category: Mapped[str] = mapped_column(String(40), default="other")
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    details_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "customer_id"],
                             ["customers.tenant_id", "customers.owner_user_id", "customers.id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_name: Mapped[str] = mapped_column(String(160), nullable=False)
    fund_code: Mapped[str | None] = mapped_column(String(32))
    amount_cny: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="sales_reported_unverified")
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class FollowUp(Base):
    __tablename__ = "followups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", "id"),
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "customer_id"],
                             ["customers.tenant_id", "customers.owner_user_id", "customers.id"]),
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "customer_id", "contact_id"],
                             ["contacts.tenant_id", "contacts.owner_user_id", "contacts.customer_id", "contacts.id"]),
        Index("ix_followup_due", "tenant_id", "owner_user_id", "status", "due_on"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    contact_id: Mapped[str | None] = mapped_column(String(36))
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    cadence_days: Mapped[int | None] = mapped_column(Integer)
    reason_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class MarketSignal(Base):
    __tablename__ = "market_signals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_key"),
        UniqueConstraint("tenant_id", "id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False)
    external_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    headline: Mapped[str] = mapped_column(String(240), nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(500))
    details_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    happened_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", "dedup_key"),
        ForeignKeyConstraint(["tenant_id", "signal_id"],
                             ["market_signals.tenant_id", "market_signals.id"]),
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "task_id"],
                             ["followups.tenant_id", "followups.owner_user_id", "followups.id"]),
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "customer_id"],
                             ["customers.tenant_id", "customers.owner_user_id", "customers.id"]),
        Index("ix_alert_inbox", "tenant_id", "owner_user_id", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    signal_id: Mapped[str | None] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_md: Mapped[str] = mapped_column(Text, nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(220), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class PendingAction(Base):
    __tablename__ = "pending_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    result: Mapped[dict | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False)
    details_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
