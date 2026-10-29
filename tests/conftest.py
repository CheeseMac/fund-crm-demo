import os
os.environ["DEV_MODE"] = "true"
os.environ["ALLOW_DEV_TENANT_REGISTRATION"] = "true"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["LLM_ENABLED"] = "false"
# 测试直接调用 workers 单元；内嵌循环会打到内存库报错，关闭
os.environ["SCHEDULER_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.main import app as api_app
from app.database import Base, get_db
import app.models  # noqa

@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    def override_db():
        with factory() as db:
            yield db
    api_app.dependency_overrides[get_db] = override_db
    # MCP 入口不经过 FastAPI 依赖注入，单独把它使用的会话工厂指到测试库
    import app.mcp_entry
    original_mcp_db = app.mcp_entry._db
    app.mcp_entry._db = factory
    with TestClient(api_app) as cli:
        yield cli
    app.mcp_entry._db = original_mcp_db
    api_app.dependency_overrides.clear()
    engine.dispose()

def register(client, slug, email):
    result = client.post("/auth/dev-register-tenant", json={
        "tenant_slug": slug, "tenant_name": f"Test {slug}",
        "email": email, "password": "DevTesting#12345",
    })
    assert result.status_code == 201, result.text
    return {"Authorization": f"Bearer {result.json()['access_token']}"}

def create_customer_and_contacts(client, h, name="华星资管"):
    resp = client.post("/customers", headers=h,
                       json={"name": name, "customer_type": "institution",
                             "tags": ["institution"]})
    assert resp.status_code == 201, resp.text
    cid = resp.json()["id"]
    contacts = []
    for nm in ("王敏", "李雷"):
        r = client.post(f"/customers/{cid}/contacts",
                        headers=h, json={"name": nm, "title": "投资经理"})
        assert r.status_code == 201, r.text
        contacts.append(r.json()["id"])
    return cid, contacts
