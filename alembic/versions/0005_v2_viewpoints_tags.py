"""V2：市场观点与用户标签字典（迁移 0005）。

Revision ID: 0005_v2_viewpoints_tags
Revises: 0004_v2_reports_market
"""
from alembic import op
from app.database import Base
import app.models  # noqa

revision = "0005_v2_viewpoints_tags"
down_revision = "0004_v2_reports_market"
branch_labels = None
depends_on = None

OWNED = ["tag_defs", "market_viewpoints"]


def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=[
        Base.metadata.tables[name] for name in OWNED
        if name in Base.metadata.tables and not bind.dialect.has_table(bind, name)
    ])
    if bind.dialect.name != "postgresql":
        return
    for name in OWNED:
        expr = ("tenant_id = nullif(current_setting('app.tenant_id', true), '') "
                "AND owner_user_id = nullif(current_setting('app.user_id', true), '')")
        op.execute(f"ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{name} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY scoped_access ON public.{name} "
                   f"FOR ALL TO crm_app USING ({expr}) WITH CHECK ({expr})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO crm_app")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name in OWNED:
            op.execute(f"DROP POLICY IF EXISTS scoped_access ON public.{name}")
            op.execute(f"ALTER TABLE public.{name} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE public.{name} DISABLE ROW LEVEL SECURITY")
    Base.metadata.drop_all(bind=bind, tables=[
        Base.metadata.tables[name] for name in OWNED if name in Base.metadata.tables
    ])
