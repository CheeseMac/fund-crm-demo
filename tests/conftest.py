import os
os.environ["DEV_MODE"] = "true"
os.environ["ALLOW_DEV_TENANT_REGISTRATION"] = "true"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["LLM_ENABLED"] = "false"

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
    with TestClient(api_app) as cli:
        yield cli
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
