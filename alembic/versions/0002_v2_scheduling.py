"""V2 PR2：调度/作业/Outbox/通知偏好（docs/05 §4）。

Revision ID: 0002_v2_scheduling
Revises: 0001_initial
"""
from alembic import op
from app.database import Base
import app.models  # noqa

revision = "0002_v2_scheduling"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

# owner 级：随销售隔离；租户级：worker/审计可见
OWNED = ["task_schedules", "user_notification_prefs"]
TENANT_WIDE = ["job_runs", "outbox_events"]
NEW_TABLES = OWNED + TENANT_WIDE


def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=[
        Base.metadata.tables[name] for name in NEW_TABLES
        if name in Base.metadata.tables and not bind.dialect.has_table(bind, name)
    ])
    if bind.dialect.name != "postgresql":
        return
    for name in NEW_TABLES:
        expr = (
            "tenant_id = nullif(current_setting('app.tenant_id', true), '')"
            + ("" if name in TENANT_WIDE
               else " AND owner_user_id = nullif(current_setting('app.user_id', true), '')")
        )
        op.execute(f"ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{name} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY scoped_access ON public.{name} "
                   f"FOR ALL TO crm_app USING ({expr}) WITH CHECK ({expr})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO crm_app")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name in NEW_TABLES:
            op.execute(f"DROP POLICY IF EXISTS scoped_access ON public.{name}")
            op.execute(f"ALTER TABLE public.{name} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE public.{name} DISABLE ROW LEVEL SECURITY")
    Base.metadata.drop_all(bind=bind, tables=[
        Base.metadata.tables[name] for name in NEW_TABLES if name in Base.metadata.tables
    ])
