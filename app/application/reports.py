"""确定性周报（docs/05 §3）：SQL ReportBuilder 先行，模型不可用也照常出报告。

口径：
- 周报永远覆盖"上一个完整自然周"（周一~周日），周一 08:45 由 Scheduler 自动生成；
- 全部指标来自数据库计数，无 AI 参与；summary_md 由指标拼装，可追溯。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select, String
from sqlalchemy.orm import Session

from app.ai.text_rules import local_today
from app.dependencies import Actor
from app.models import (
    Customer, Visit, Need, FollowUp, Holding, Alert, GeneratedReport, uid,
)


def week_bounds(day: date | None = None) -> tuple[date, date]:
    """返回 day 所在自然周的周一与周日。"""
    day = day or local_today()
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def previous_week_bounds(day: date | None = None) -> tuple[date, date]:
    monday, _ = week_bounds(day)
    prev_monday = monday - timedelta(days=7)
    return prev_monday, prev_monday + timedelta(days=6)


def _in_period_dt(column, start: date, end: date):
    # created_at 为 UTC 时间戳（SQLite 存 naive）；按自然日字符串比较避免时区误差
    return func.substr(func.cast(column, String), 1, 10).between(
        start.isoformat(), end.isoformat())


def build_weekly_metrics(db: Session, actor: Actor,
                         start: date, end: date) -> dict:
    def count(stmt) -> int:
        return db.scalar(stmt) or 0

    tid, uid_value = actor.tenant_id, actor.user_id
    visits = count(select(func.count()).select_from(Visit).where(
        Visit.tenant_id == tid, Visit.owner_user_id == uid_value,
        Visit.occurred_on.between(start, end)))
    needs_created = count(select(func.count()).select_from(Need).where(
        Need.tenant_id == tid, Need.owner_user_id == uid_value,
        _in_period_dt(Need.created_at, start, end)))
    tasks_created = count(select(func.count()).select_from(FollowUp).where(
        FollowUp.tenant_id == tid, FollowUp.owner_user_id == uid_value,
        _in_period_dt(FollowUp.created_at, start, end)))
    holdings_created = count(select(func.count()).select_from(Holding).where(
        Holding.tenant_id == tid, Holding.owner_user_id == uid_value,
        _in_period_dt(Holding.created_at, start, end)))
    alerts_created = count(select(func.count()).select_from(Alert).where(
        Alert.tenant_id == tid, Alert.owner_user_id == uid_value,
        _in_period_dt(Alert.created_at, start, end)))
    market_hits = count(select(func.count()).select_from(Alert).where(
        Alert.tenant_id == tid, Alert.owner_user_id == uid_value,
        Alert.kind == "market_attention",
        _in_period_dt(Alert.created_at, start, end)))
    today = local_today()
    overdue = count(select(func.count()).select_from(FollowUp).where(
        FollowUp.tenant_id == tid, FollowUp.owner_user_id == uid_value,
        FollowUp.status == "open", FollowUp.due_on < today))
    upcoming = count(select(func.count()).select_from(FollowUp).where(
        FollowUp.tenant_id == tid, FollowUp.owner_user_id == uid_value,
        FollowUp.status == "open",
        FollowUp.due_on.between(today, today + timedelta(days=7))))
    customers_total = count(select(func.count()).select_from(Customer).where(
        Customer.tenant_id == tid, Customer.owner_user_id == uid_value))
    open_needs = count(select(func.count()).select_from(Need).where(
        Need.tenant_id == tid, Need.owner_user_id == uid_value, Need.status == "open"))
    closed_needs = count(select(func.count()).select_from(Need).where(
        Need.tenant_id == tid, Need.owner_user_id == uid_value, Need.status == "closed"))
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "visits": visits, "needs_created": needs_created,
        "tasks_created": tasks_created, "holdings_created": holdings_created,
        "alerts_created": alerts_created, "market_hits": market_hits,
        "overdue_now": overdue, "upcoming_7d": upcoming,
        "customers_total": customers_total,
        "open_needs": open_needs, "closed_needs": closed_needs,
    }


def _summary_md(m: dict) -> str:
    p = m["period"]
    return (
        f"# 周报 {p['start']} ~ {p['end']}\n\n"
        f"## 本期动作\n"
        f"- 客户拜访：{m['visits']} 次\n"
        f"- 新建需求：{m['needs_created']} 条；新建待办：{m['tasks_created']} 条\n"
        f"- 口述持仓申报：{m['holdings_created']} 条（均未核验）\n\n"
        f"## 提醒与风险提示\n"
        f"- 站内提醒新增：{m['alerts_created']} 条（其中市场事件命中 {m['market_hits']} 条）\n"
        f"- 当前逾期待办：{m['overdue_now']} 条；未来 7 天到期：{m['upcoming_7d']} 条\n\n"
        f"## 覆盖情况（快照）\n"
        f"- 客户总数：{m['customers_total']}；开放需求：{m['open_needs']}；已关闭需求：{m['closed_needs']}\n\n"
        f"> 本报告全部指标由 SQL 统计生成（source=sql_builder），无模型参与；"
        f"持仓均为销售口述、未核验。")


def upsert_weekly_report(db: Session, actor: Actor,
                         day: date | None = None) -> tuple[GeneratedReport, bool]:
    """生成（或取回已存在的）上一完整周周报；同周幂等。"""
    start, end = previous_week_bounds(day)
    existing = db.scalar(select(GeneratedReport).where(
        GeneratedReport.tenant_id == actor.tenant_id,
        GeneratedReport.owner_user_id == actor.user_id,
        GeneratedReport.kind == "weekly",
        GeneratedReport.period_start == start,
    ))
    if existing:
        return existing, False
    metrics = build_weekly_metrics(db, actor, start, end)
    report = GeneratedReport(
        id=uid(), tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
        kind="weekly", period_start=start, period_end=end,
        metrics=metrics, summary_md=_summary_md(metrics), source="sql_builder",
    )
    db.add(report)
    db.commit()
    return report, True


def list_reports(db: Session, actor: Actor, kind: str = "weekly",
                 limit: int = 12) -> list[dict]:
    rows = db.scalars(select(GeneratedReport).where(
        GeneratedReport.tenant_id == actor.tenant_id,
        GeneratedReport.owner_user_id == actor.user_id,
        GeneratedReport.kind == kind,
    ).order_by(GeneratedReport.period_start.desc()).limit(limit)).all()
    return [{"id": r.id, "period_start": r.period_start.isoformat(),
             "period_end": r.period_end.isoformat(), "metrics": r.metrics,
             "summary_md": r.summary_md, "source": r.source,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]


def dashboard(db: Session, actor: Actor) -> dict:
    """看板数据：本周至今 + 近 8 周趋势 + 最新周报。"""
    today = local_today()
    monday, sunday = week_bounds(today)
    this_week = build_weekly_metrics(db, actor, monday, today)
    trend = []
    for i in range(7, -1, -1):
        ws = monday - timedelta(days=7 * i)
        we = ws + timedelta(days=6)
        m = build_weekly_metrics(db, actor, ws, we)
        trend.append({"week_start": ws.isoformat(),
                      "visits": m["visits"], "needs_created": m["needs_created"],
                      "alerts": m["alerts_created"]})
    latest = db.scalar(select(GeneratedReport).where(
        GeneratedReport.tenant_id == actor.tenant_id,
        GeneratedReport.owner_user_id == actor.user_id,
        GeneratedReport.kind == "weekly",
    ).order_by(GeneratedReport.period_start.desc()))
    return {
        "today": today.isoformat(),
        "this_week": this_week,
        "trend_weeks": trend,
        "latest_report": ({"id": latest.id,
                           "period_start": latest.period_start.isoformat(),
                           "period_end": latest.period_end.isoformat(),
                           "summary_md": latest.summary_md,
                           "metrics": latest.metrics,
                           "created_at": latest.created_at.isoformat() if latest.created_at else None}
                          if latest else None),
    }
