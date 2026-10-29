from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from app.config import settings

class Base(DeclarativeBase):
    pass

kwargs = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    # timeout：后台维护循环与请求线程并发时等待写锁，而非立刻报 database is locked
    kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
engine = create_engine(settings.database_url, **kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

@event.listens_for(Session, "after_begin")
def set_transaction_rls_context(session, transaction, connection):
    """SET LOCAL semantics via set_config(..., true), refreshed after each COMMIT."""
    if connection.dialect.name != "postgresql":
        return
    tid = session.info.get("tenant_id")
    uid = session.info.get("user_id")
    if tid and uid:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tid, true), "
                 "set_config('app.user_id', :uid, true)"),
            {"tid": str(tid), "uid": str(uid)},
        )

def get_db():
    with SessionLocal() as db:
        yield db
