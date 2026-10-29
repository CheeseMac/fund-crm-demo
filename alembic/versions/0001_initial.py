"""Initial CRM schema + fail-closed PostgreSQL row security.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
from app.database import Base
import app.models  # noqa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

OWNED = [
    "customers", "contacts", "visits", "needs", "holdings", "followups",
    "alerts", "pending_actions", "audit_logs",
]
TENANT_WIDE = ["users", "market_signals"]

def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name != "postgresql":
        return
    for name in OWNED + TENANT_WIDE:
        if name in OWNED:
            expr = (
                "tenant_id = nullif(current_setting('app.tenant_id', true), '') "
                "AND owner_user_id = nullif(current_setting('app.user_id', true), '')"
            )
        else:
            expr = "tenant_id = nullif(current_setting('app.tenant_id', true), '')"
        op.execute(f"ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{name} FORCE ROW LEVEL SECURITY")
        if name == "audit_logs":
            op.execute(f"CREATE POLICY audit_read ON public.{name} "
                       f"FOR SELECT TO crm_app USING ({expr})")
            op.execute(f"CREATE POLICY audit_append ON public.{name} "
                       f"FOR INSERT TO crm_app WITH CHECK ({expr})")
        else:
            op.execute(f"CREATE POLICY scoped_access ON public.{name} "
                       f"FOR ALL TO crm_app USING ({expr}) WITH CHECK ({expr})")
    op.execute("GRANT USAGE ON SCHEMA public TO crm_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO crm_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO crm_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
               "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO crm_app")
    op.execute("REVOKE UPDATE, DELETE ON public.audit_logs FROM crm_app")
    # A login only needs the public tenant directory keys; the API must not mutate
    # tenants or enumerate organization names using the unprivileged DB role.
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON public.tenants FROM crm_app")
    op.execute("GRANT SELECT (id, slug) ON public.tenants TO crm_app")

def downgrade():
    # Downgrade intentionally destroys all CRM content; NEVER run against production.
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
