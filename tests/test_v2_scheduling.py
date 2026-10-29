"""PR2（docs/05 §11）：通知偏好持久化、Outbox 幂等、Scheduler/Runner 行为。"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
import app.models  # noqa
from app.models import TaskSchedule, JobRun, OutboxEvent, UserNotificationPrefs, uid
from app.workers.scheduling import (
    ensure_default_schedule, materialize_due_schedules, next_occurrence,
)
from app.workers.runner import run_due_jobs
from app.workers.outbox import process_one
from app.ai.text_rules import local_today

from tests.conftest import register, create_customer_and_contacts


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _naive_utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── 通知偏好（服务端持久化，替代 localStorage 方案）──

def test_prefs_roundtrip_and_reconcile_uses_server_prefs(client):
    h = register(client, "prefs-tenant", "p@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    tomorrow = (local_today() + timedelta(days=1)).isoformat()
    r = client.post("/tasks", headers=h, json={
        "customer_id": cid, "due_on": tomorrow, "reason_md": "明日跟进（偏好测试）"})
    assert r.status_code == 201, r.text

    got = client.get("/api/v2/me/notification-preferences", headers=h)
    assert got.status_code == 200 and got.json()["lead_days"] == 0

    put = client.put("/api/v2/me/notification-preferences", headers=h,
                     json={"lead_days": 1})
    assert put.status_code == 200 and put.json()["lead_days"] == 1

    # 不带 lead_days 参数的 reconcile 使用服务端偏好（提前 1 天口径）
    rec = client.post("/reminders/reconcile", headers=h)
    assert rec.status_code == 200, rec.text
    assert rec.json() == {"alerts_created": 1, "lead_days": 1}
    # 再次同步不重复（dedup_key 幂等）
    rec2 = client.post("/reminders/reconcile", headers=h)
    assert rec2.json()["alerts_created"] == 0

    # 偏好改回 0：未到期任务不再提前提醒
    client.put("/api/v2/me/notification-preferences", headers=h, json={"lead_days": 0})
    rec3 = client.post("/reminders/reconcile", headers=h)
    assert rec3.json() == {"alerts_created": 0, "lead_days": 0}


# ── Outbox：市场事件发布即写事件，重复处理无副作用 ──

def test_market_publish_outbox_flow_idempotent(client):
    h = register(client, "outbox-tenant", "o@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    client.post(f"/customers/{cid}/holdings", headers=h, json={
        "product_name": "红利ETF", "fund_code": "510880",
        "amount_cny": 100000, "as_of": date.today().isoformat(),
        "tags": ["dividend"]})
    event = {
        "external_key": "OUTBOX-TEST-001", "kind": "style_rotation",
        "severity": 4, "tags": ["dividend"],
        "headline": "演示：红利拥挤（outbox 测试）",
        "happened_on": date.today().isoformat(),
    }
    r = client.post("/market/signals", headers=h, json=event)
    assert r.status_code == 201 and r.json()["alerts_created"] == 1, r.text
    signal_id = r.json()["id"]

    # 重复 reconcile（等价于 Outbox 重放）：不产生新提醒
    r2 = client.post(f"/market/signals/{signal_id}/reconcile", headers=h)
    assert r2.status_code == 200 and r2.json()["alerts_created"] == 0
    # 相同 external_key 再次发布被拒
    assert client.post("/market/signals", headers=h, json=event).status_code == 409
    # 提醒只有一条
    assert len(client.get("/alerts", headers=h).json()) == 1


def test_outbox_unknown_subject_marks_processed():
    factory = _factory()
    with factory() as db:
        db.add(OutboxEvent(event_type="market.signal.accepted.v1",
                           tenant_id="t1", actor_id=None,
                           payload={"signal_id": "no-such-signal"},
                           idempotency_key="k-unknown"))
        db.commit()
        event_id = db.query(OutboxEvent).one().event_id
    # 信号不存在：无副作用，事件标记 processed（避免死循环重试）
    assert process_one(factory, event_id) == 0
    with factory() as db:
        assert db.get(OutboxEvent, event_id).delivery_status == "processed"
    # 重复处理为幂等 no-op
    assert process_one(factory, event_id) == 0


# ── Scheduler / Runner ──

def _make_user_with_task(factory, tenant, user, due_in_days):
    from app.models import User, Tenant, Customer, FollowUp
    from app.security import hash_password
    with factory() as db:
        db.add(Tenant(id=tenant, slug=tenant, name="T"))
        db.add(User(id=user, tenant_id=tenant, email=f"{user}@example.test",
                    password_hash=hash_password("DevTesting#12345"), role="sales"))
        db.commit()
        db.add(Customer(id=f"c-{user}", tenant_id=tenant, owner_user_id=user,
                        name="华星资管", customer_type="institution"))
        db.add(FollowUp(id=f"f-{user}", tenant_id=tenant, owner_user_id=user,
                        customer_id=f"c-{user}",
                        due_on=local_today() + timedelta(days=due_in_days),
                        reason_md="到期提醒作业测试"))
        db.commit()


def test_scheduler_materialize_and_run_daily_reminder():
    factory = _factory()
    _make_user_with_task(factory, "t1", "u1", due_in_days=0)  # 今天到期
    with factory() as db:
        schedule = ensure_default_schedule(db, "t1", "u1")
        past = _naive_utcnow() - timedelta(days=1)
        schedule.next_run_at = past
        db.commit()
        due_at = past
    with factory() as db:
        assert materialize_due_schedules(db) == 1
    # 同一逻辑时点再次物化：UNIQUE(schedule_id, scheduled_for) 拦截，幂等
    with factory() as db:
        s = db.get(TaskSchedule, schedule.id)
        s.next_run_at = due_at  # 人为拨回同一时点
        db.commit()
    with factory() as db:
        assert materialize_due_schedules(db) == 0
        jobs = db.query(JobRun).all()
        assert len(jobs) == 1

    results = run_due_jobs(factory)
    assert results and results[0]["status"] == "succeeded", results
    assert results[0]["alerts_created"] == 1  # lead_days=0：今天到期命中
    assert run_due_jobs(factory) == []         # 重复执行无新增（提醒 dedup）


def test_daily_reminder_respects_lead_days_prefs():
    factory = _factory()
    _make_user_with_task(factory, "t2", "u2", due_in_days=2)  # 两天后到期
    with factory() as db:
        db.add(UserNotificationPrefs(tenant_id="t2", user_id="u2", lead_days=3,
                                     channels=["inbox"]))
        db.add(JobRun(id=uid(), tenant_id="t2", owner_user_id="u2",
                      job_type="daily_reminder", payload={},
                      scheduled_for=_naive_utcnow(), available_at=_naive_utcnow(),
                      idempotency_key="test-lead-job"))
        db.commit()
    results = run_due_jobs(factory)
    assert results[0]["status"] == "succeeded"
    assert results[0]["lead_days"] == 3
    assert results[0]["alerts_created"] == 1  # 提前 3 天口径命中两天后任务
