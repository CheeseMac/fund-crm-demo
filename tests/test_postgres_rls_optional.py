"""Real PG RLS smoke test. Requires TWO DSNs to a disposable migrated test database.

RLS_TEST_DATABASE_URL: restricted crm_app credentials
RLS_TEST_ADMIN_URL: migration/owner role for fixture seeding
DO NOT POINT THESE AT PRODUCTION!
"""
import os
import uuid
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, DBAPIError

requires = bool(os.getenv("RLS_TEST_DATABASE_URL") and os.getenv("RLS_TEST_ADMIN_URL"))

@pytest.mark.skipif(not requires, reason="Set both RLS_TEST_*_URL for disposable PostgreSQL")
def test_postgres_rls_enforces_tenant_and_owner():
    app_engine = create_engine(os.environ["RLS_TEST_DATABASE_URL"])
    owner_engine = create_engine(os.environ["RLS_TEST_ADMIN_URL"])
    assert app_engine.dialect.name == "postgresql"
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    ua, ub, uc = [str(uuid.uuid4()) for _ in range(3)]
    ca, cb, cc = [str(uuid.uuid4()) for _ in range(3)]
    with owner_engine.begin() as c:
        for tid in (a, b):
            c.execute(text("INSERT INTO tenants(id,slug,name,created_at)"
                           " VALUES(:id,:slug,'Disposable RLS test',NOW())"),
                      {"id":tid,"slug":"rls-test-"+tid[:12]})
        for uid, tid in [(ua,a),(ub,a),(uc,b)]:
            c.execute(text("INSERT INTO users(id,tenant_id,email,password_hash,role,is_active,created_at)"
                           " VALUES (:u,:t,:email,'dummy','sales',true,NOW())"),
                      {"u":uid,"t":tid,"email":uid+"@test.invalid"})
        for cid, uid, tid, name in [(ca,ua,a,"A"),(cb,ub,a,"B"),(cc,uc,b,"C")]:
            c.execute(text("INSERT INTO customers(id,tenant_id,owner_user_id,name,"
                           "customer_type,tags,background_md,created_at)"
                           " VALUES(:c,:t,:u,:name,'institution','[]','',NOW())"),
                      {"c":cid,"t":tid,"u":uid,"name":name})
    def ids(tid=None, uid=None):
        with app_engine.begin() as c:
            if tid and uid:
                c.execute(text("SELECT set_config('app.tenant_id', :t, true), "
                               "set_config('app.user_id', :u, true)"),
                          {"t":tid,"u":uid})
            return c.execute(text("SELECT id FROM customers")).scalars().all()
    assert ids() == []
    assert ids(a,ua) == [ca]
    assert ids(a,ub) == [cb]
    assert ids(b,uc) == [cc]

    # Non-owner application role must not alter tenant records.
    with pytest.raises(DBAPIError):
        with app_engine.begin() as c:
            c.execute(text("UPDATE tenants SET name='tampered' WHERE id=:id"),{"id":a})
    # Even when RLS grants INSERT for a user's own row, cross-owner FK stops it.
    with pytest.raises(IntegrityError):
        with app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true), "
                           "set_config('app.user_id', :u, true)"),{"t":a,"u":ua})
            c.execute(text("INSERT INTO visits(id,tenant_id,owner_user_id,customer_id,"
                           "occurred_on,channel,body_md,created_at)"
                           " VALUES(:id,:t,:u,:c,CURRENT_DATE,'meeting','',NOW())"),
                      {"id":str(uuid.uuid4()),"t":a,"u":ua,"c":cb})
    app_engine.dispose()
    owner_engine.dispose()
