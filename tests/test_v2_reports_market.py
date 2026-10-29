"""PR5 回归：确定性周报（口径/幂等/计划作业）与市场观察候选流水线（docs/05 §3）。"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
import app.models  # noqa
from app.models import TaskSchedule, GeneratedReport, uid
from app.workers.scheduling import ensure_default_schedule, materialize_due_schedules
from app.workers.runner import run_due_jobs
from app.ai.text_rules import local_today

from tests.conftest import register, create_customer_and_contacts


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(c, r):
        c.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _make_user(factory, tenant, user):
    from app.models import User, Tenant
    from app.security import hash_password
    with factory() as db:
        db.add(Tenant(id=tenant, slug=tenant, name="T"))
        db.add(User(id=user, tenant_id=tenant, email=f"{user}@example.test",
                    password_hash=hash_password("DevTesting#12345"), role="sales"))
        db.commit()


# ── 确定性周报 ──

def test_weekly_report_deterministic_and_idempotent(client):
    h = register(client, "rpt-a", "rpt@example.test")
    create_customer_and_contacts(client, h)
    # 本周造数据：1 次拜访 + 1 条需求（经确认的提案）
    ir = client.post("/assistant/interpret", headers=h, json={
        "message": "今天拜访了王敏，需求：关注红利低波；30天后回访"})
    client.post(f"/assistant/confirm/{ir.json()['pending_action_id']}", headers=h)
    # 手工生成"上周"周报：上周无动作 → 动作类指标为 0 但结构完整
    r1 = client.post("/api/v2/reports/generate", headers=h)
    assert r1.status_code == 200, r1.text
    report = r1.json()["report"]
    assert report["metrics"]["visits"] == 0
    assert report["period_start"] < local_today().isoformat()
    assert "SQL 统计" in report["summary_md"] and "无模型参与" in report["summary_md"]
    # 幂等：再生成一次，返回同一份（created=False）
    r2 = client.post("/api/v2/reports/generate", headers=h)
    assert r2.json()["report"]["id"] == report["id"]
    assert r2.json()["job"]["created"] is False
    # 报告列表与看板
    assert len(client.get("/api/v2/reports", headers=h).json()) == 1
    board = client.get("/api/v2/dashboard", headers=h).json()
    assert board["this_week"]["visits"] == 1          # 今天的拜访计入本周
    assert board["this_week"]["customers_total"] == 1
    assert len(board["trend_weeks"]) == 8
    assert board["latest_report"]["id"] == report["id"]


def test_weekly_report_scheduled_via_scheduler():
    """默认计划含周一 08:45 周报；物化+执行产出上周报告（同周幂等）。"""
    factory = _factory()
    _make_user(factory, "t9", "u9")
    with factory() as db:
        ensure_default_schedule(db, "t9", "u9")
        weekly = db.scalar(select(TaskSchedule).where(
            TaskSchedule.tenant_id == "t9", TaskSchedule.kind == "weekly_report"))
        assert weekly is not None and weekly.weekday == 0
        assert (weekly.hour, weekly.minute) == (8, 45)
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
        weekly.next_run_at = past
        db.commit()
    with factory() as db:
        assert materialize_due_schedules(db) >= 1
        # 同一时点重复物化幂等
        w = db.get(TaskSchedule, weekly.id)
        w.next_run_at = past
        db.commit()
    with factory() as db:
        assert materialize_due_schedules(db) == 0
        jobs = db.scalars(select(__import__("app.models", fromlist=["JobRun"]).JobRun)
                          .where(__import__("app.models", fromlist=["JobRun"]).JobRun.job_type == "weekly_report")).all()
        assert len(jobs) == 1
    results = run_due_jobs(factory)
    weekly_result = [r for r in results if r.get("report_id")]
    assert weekly_result and weekly_result[0]["status"] == "succeeded"
    with factory() as db:
        reports = db.scalars(select(GeneratedReport).where(
            GeneratedReport.owner_user_id == "u9")).all()
        assert len(reports) == 1
        assert reports[0].period_end < local_today()  # 覆盖上一完整周


# ── 市场观察 → 候选 → 复核 → 正式事件 ──

def test_observation_pipeline_thresholds(client):
    h = register(client, "obs-a", "obs@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    client.post(f"/customers/{cid}/holdings", headers=h, json={
        "product_name": "红利ETF", "fund_code": "510880",
        "amount_cny": 100000, "as_of": local_today().isoformat(), "tags": ["dividend"]})
    # 未达阈值：不生成候选
    small = client.post("/internal/market/observations", headers=h, json={
        "provider": "演示研究台", "external_key": "OBS-SMALL-1",
        "symbol_or_topic": "红利", "observed_on": local_today().isoformat(),
        "change_pct": 1.2})
    assert small.status_code == 201 and small.json()["candidate_id"] is None
    # 达到高阈值：severity 4 候选
    big = client.post("/internal/market/observations", headers=h, json={
        "provider": "演示研究台", "external_key": "OBS-BIG-1",
        "symbol_or_topic": "红利", "observed_on": local_today().isoformat(),
        "change_pct": -5.2})
    assert big.status_code == 201, big.text
    cand_id = big.json()["candidate_id"]
    candidates = client.get("/api/v2/market/candidates", headers=h,
                            params={"status": "pending"}).json()
    assert any(c["id"] == cand_id and c["severity"] == 4 and c["tags"] == ["dividend"]
               for c in candidates)
    # 重复外部键幂等拒绝
    assert client.post("/internal/market/observations", headers=h, json={
        "provider": "演示研究台", "external_key": "OBS-BIG-1",
        "symbol_or_topic": "红利", "observed_on": local_today().isoformat(),
        "change_pct": -5.2}).status_code == 409
    # 接受候选 → 正式事件（Outbox 路径）→ 命中客户提醒
    accept = client.post(f"/api/v2/market/candidates/{cand_id}/accept", headers=h)
    assert accept.status_code == 200, accept.text
    payload = accept.json()
    assert payload["status"] == "accepted" and payload["alerts_created"] == 1
    alerts = client.get("/alerts", headers=h).json()
    assert any(a["kind"] == "market_attention" and "红利" in a["reason_md"]
               for a in alerts)
    # 已处理候选不可重复决策
    assert client.post(f"/api/v2/market/candidates/{cand_id}/reject",
                       headers=h).status_code == 409


def test_observation_reject_and_quality_gate(client):
    h = register(client, "obs-b", "obsb@example.test")
    create_customer_and_contacts(client, h)
    obs = client.post("/internal/market/observations", headers=h, json={
        "provider": "演示研究台", "external_key": "OBS-R-1",
        "symbol_or_topic": "科技", "observed_on": local_today().isoformat(),
        "change_pct": 4.0})
    cand_id = obs.json()["candidate_id"]
    assert cand_id  # ≥3 生成候选
    rejected = client.post(f"/api/v2/market/candidates/{cand_id}/reject", headers=h)
    assert rejected.json()["status"] == "rejected"
    assert client.get("/alerts", headers=h).json() == []  # 拒绝不产生任何提醒
    # 质量不合格的观察直接丢弃，不生成候选
    bad = client.post("/internal/market/observations", headers=h, json={
        "provider": "演示研究台", "external_key": "OBS-BAD-1",
        "symbol_or_topic": "科技", "observed_on": local_today().isoformat(),
        "change_pct": 9.9, "quality": "rejected"})
    assert bad.json()["candidate_id"] is None
    assert client.get("/api/v2/market/candidates", headers=h).json() == [] or all(
        c["status"] != "pending" for c in client.get(
            "/api/v2/market/candidates", headers=h).json())
