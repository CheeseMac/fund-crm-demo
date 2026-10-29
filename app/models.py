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


class UserNotificationPrefs(Base):
    """服务端持久化的提醒设置（docs/05 §4）：跨设备一致的 lead_days 等偏好。"""
    __tablename__ = "user_notification_prefs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    lead_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    channels: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    digest_mode: Mapped[str] = mapped_column(String(20), default="instant", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TaskSchedule(Base):
    """持久化定时计划（docs/05 §4）：由内部 Scheduler 物化为 JobRun，不依赖用户登录。"""
    __tablename__ = "task_schedules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", "kind"),
        ForeignKeyConstraint(["tenant_id", "owner_user_id"], ["users.tenant_id", "users.id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # daily_reminder / weekly_report
    hour: Mapped[int] = mapped_column(Integer, nullable=False)
    minute: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    weekday: Mapped[int | None] = mapped_column(Integer)  # 0=Mon；None=每天
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    misfire_policy: Mapped[str] = mapped_column(String(20), default="run_once", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobRun(Base):
    """单次作业：物化幂等（schedule+scheduled_for 唯一），领取带租约，可重试。"""
    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint("schedule_id", "scheduled_for"),
        UniqueConstraint("idempotency_key"),
        Index("ix_job_runs_claim", "status", "available_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    schedule_id: Mapped[str | None] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(36))
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(400), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEvent(Base):
    """事务性 Outbox（docs/05 §4）：业务写入与事件同事务；Worker 至少一次+幂等消费。"""
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        Index("ix_outbox_pending", "delivery_status", "occurred_at"),
    )
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    idempotency_key: Mapped[str] = mapped_column(String(400), nullable=False)
    delivery_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Meeting(Base):
    """客户会议记录（PR4 占位）：转写/纪要生成在 PR4 接入 Transcriber 端口后补全。"""
    __tablename__ = "meetings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", "id"),
        UniqueConstraint("tenant_id", "owner_user_id", "customer_id", "id"),
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "customer_id"],
                             ["customers.tenant_id", "customers.owner_user_id", "customers.id"]),
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "customer_id", "contact_id"],
                             ["contacts.tenant_id", "contacts.owner_user_id",
                              "contacts.customer_id", "contacts.id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    contact_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    minutes_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    minutes_status: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MediaAsset(Base):
    """多媒体资产（docs/05 §5.2）：元数据在 PG，字节在私有对象存储。"""
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", "id"),
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "customer_id"],
                             ["customers.tenant_id", "customers.owner_user_id", "customers.id"]),
        ForeignKeyConstraint(["tenant_id", "owner_user_id", "customer_id", "meeting_id"],
                             ["meetings.tenant_id", "meetings.owner_user_id",
                              "meetings.customer_id", "meetings.id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    meeting_id: Mapped[str | None] = mapped_column(String(36))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(20), default="other", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(400))
    state: Mapped[str] = mapped_column(String(20), default="pending_upload", nullable=False)
    scan_result: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaProcessingJob(Base):
    """媒体处理留痕（扫描/探针/转写占位）：stage/status/attempt/error。"""
    __tablename__ = "media_processing_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    stage: Mapped[str] = mapped_column(String(20), nullable=False)  # scan / probe / asr
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GeneratedReport(Base):
    """确定性周报（docs/05 §3）：SQL 统计快照，模型仅可选叙述层。"""
    __tablename__ = "generated_reports"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", "kind", "period_start"),
        ForeignKeyConstraint(["tenant_id", "owner_user_id"], ["users.tenant_id", "users.id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="weekly", nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    summary_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="sql_builder", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketObservation(Base):
    """市场事实输入（docs/05 §3）：带来源与时效的观察，不等于交易信号。"""
    __tablename__ = "market_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_key"),
        UniqueConstraint("tenant_id", "id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    external_key: Mapped[str] = mapped_column(String(160), nullable=False)
    symbol_or_topic: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="index_change", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    quality: Mapped[str] = mapped_column(String(20), default="raw", nullable=False)
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketSignalCandidate(Base):
    """候选市场事件（docs/05 §3）：阈值规则产出，必须人工复核后才成为正式事件。"""
    __tablename__ = "market_signal_candidates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "observation_id"),
        ForeignKeyConstraint(["tenant_id", "observation_id"],
                             ["market_observations.tenant_id", "market_observations.id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    observation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    headline: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String(36))
    signal_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TagDef(Base):
    """用户自管理的主题标签字典：观点与客户（需求/持仓）共用同一套标签。"""
    __tablename__ = "tag_defs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", "key"),
        ForeignKeyConstraint(["tenant_id", "owner_user_id"], ["users.tenant_id", "users.id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    key: Mapped[str] = mapped_column(String(40), nullable=False)        # 规范键：dividend
    label: Mapped[str] = mapped_column(String(60), nullable=False)      # 显示名：红利
    aliases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # 别名：高股息
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketViewpoint(Base):
    """指定日期的市场观点：谁说的、什么内容、挂哪些标签；仅为观点留痕，不构成事件。"""
    __tablename__ = "market_viewpoints"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", "id"),
        ForeignKeyConstraint(["tenant_id", "owner_user_id"], ["users.tenant_id", "users.id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    viewpoint_date: Mapped[date] = mapped_column(Date, nullable=False)
    author_type: Mapped[str] = mapped_column(String(20), default="self", nullable=False)
    author_name: Mapped[str] = mapped_column(String(80), default="本人", nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_note: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
