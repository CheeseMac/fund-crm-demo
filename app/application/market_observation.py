"""市场观察流水线（docs/05 §3）：观察 → 质量检查 → 阈值候选 → 人工复核 → 正式事件。

关键边界：观察数据只是"带来源的事实"；只有管理员**接受**候选后才经
publish_signal（事务性 Outbox）成为正式事件并匹配客户。阈值规则版本化、
可解释；无数据/低质量输入不会产生任何客户提醒。
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.text_rules import TAG_ALIASES
from app.dependencies import Actor
from app.models import (
    MarketObservation, MarketSignalCandidate, AuditLog, uid,
)
from app.schemas import MarketSignalIn

RULE_VERSION = "threshold-v1"
# 阈值规则：|涨跌幅| ≥5% → severity 4；≥3% → severity 3（docs/05 §6.2：宁缺毋滥）
THRESHOLD_HIGH = 5.0
THRESHOLD_MID = 3.0


def ingest_observation(db: Session, actor: Actor, provider: str, external_key: str,
                       symbol_or_topic: str, observed_on: date,
                       change_pct: float, note: str = "",
                       quality: str = "validated") -> dict:
    if db.scalar(select(MarketObservation.id).where(
            MarketObservation.tenant_id == actor.tenant_id,
            MarketObservation.external_key == external_key)):
        raise HTTPException(409, "该观察外部标识已入库（幂等拒绝）")
    if quality not in ("raw", "validated", "rejected"):
        raise HTTPException(422, "quality 仅允许 raw/validated/rejected")
    observation = MarketObservation(
        id=uid(), tenant_id=actor.tenant_id, provider=provider[:80],
        external_key=external_key[:160], symbol_or_topic=symbol_or_topic[:80],
        kind="topic_change", quality=quality, observed_on=observed_on,
        payload={"change_pct": change_pct, "note": note[:500]},
    )
    db.add(observation)
    db.flush()
    candidate = None
    if quality == "rejected":
        verdict = "质量不合格，丢弃"
    else:
        magnitude = abs(float(change_pct))
        if magnitude >= THRESHOLD_HIGH:
            severity = 4
        elif magnitude >= THRESHOLD_MID:
            severity = 3
        else:
            severity = 0
        if severity > 0:
            tag = TAG_ALIASES.get(symbol_or_topic, symbol_or_topic)
            direction = "上行" if change_pct > 0 else "下行"
            candidate = MarketSignalCandidate(
                id=uid(), tenant_id=actor.tenant_id, observation_id=observation.id,
                rule_version=RULE_VERSION, severity=severity, tags=[tag],
                headline=(f"观察候选：{symbol_or_topic}主题{direction}"
                          f"{abs(change_pct):.1f}%（{provider}，{observed_on}）"),
                status="pending",
            )
            db.add(candidate)
            verdict = f"超过阈值，生成 severity={severity} 候选待复核"
        else:
            verdict = f"|{change_pct:.1f}%| 未达阈值（≥{THRESHOLD_MID}%），不生成候选"
    db.add(AuditLog(tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                    action="ingest_observation", resource_type="market_observation",
                    resource_id=observation.id,
                    details_hash=hashlib.sha256(external_key.encode()).hexdigest()))
    db.commit()
    return {"observation_id": observation.id, "verdict": verdict,
            "candidate_id": candidate.id if candidate else None}


def list_candidates(db: Session, actor: Actor, status: str | None = None) -> list[dict]:
    q = select(MarketSignalCandidate).where(
        MarketSignalCandidate.tenant_id == actor.tenant_id)
    if status:
        q = q.where(MarketSignalCandidate.status == status)
    rows = db.scalars(q.order_by(MarketSignalCandidate.created_at.desc()).limit(50)).all()
    out = []
    for c in rows:
        obs = db.get(MarketObservation, c.observation_id)
        out.append({
            "id": c.id, "severity": c.severity, "tags": c.tags,
            "headline": c.headline, "status": c.status, "rule_version": c.rule_version,
            "signal_id": c.signal_id,
            "observation": ({"provider": obs.provider,
                             "external_key": obs.external_key,
                             "topic": obs.symbol_or_topic,
                             "change_pct": (obs.payload or {}).get("change_pct"),
                             "observed_on": obs.observed_on.isoformat(),
                             "quality": obs.quality} if obs else None),
        })
    return out


def decide_candidate(db: Session, actor: Actor, candidate_id: str, accept: bool) -> dict:
    candidate = db.scalar(select(MarketSignalCandidate).where(
        MarketSignalCandidate.id == candidate_id,
        MarketSignalCandidate.tenant_id == actor.tenant_id))
    if candidate is None:
        raise HTTPException(404, "候选不存在")
    if candidate.status != "pending":
        raise HTTPException(409, f"候选已处理（{candidate.status}）")
    candidate.reviewer_id = actor.user_id
    candidate.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if not accept:
        candidate.status = "rejected"
        db.commit()
        return {"candidate_id": candidate.id, "status": "rejected",
                "signal_id": None, "alerts_created": 0}
    obs = db.get(MarketObservation, candidate.observation_id)
    from app.application.market import publish_signal
    result = publish_signal(db, actor, MarketSignalIn(
        external_key=f"obs-{obs.external_key}",
        kind="style_rotation", severity=candidate.severity, tags=candidate.tags,
        headline=candidate.headline,
        details_md=f"来源：{obs.provider}；观察日：{obs.observed_on}；"
                   f"涨跌幅：{(obs.payload or {}).get('change_pct')}%；规则：{candidate.rule_version}。"
                   f"经人工复核接受。虚构演示数据。",
        evidence_url=None, happened_on=obs.observed_on))
    candidate.status = "accepted"
    candidate.signal_id = result["id"]
    db.commit()
    return {"candidate_id": candidate.id, "status": "accepted",
            "signal_id": result["id"], "alerts_created": result["alerts_created"]}
