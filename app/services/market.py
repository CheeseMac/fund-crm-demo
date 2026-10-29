"""Market-event cross-reference: manually reviewed market observations, NOT trading signals."""
from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from app.models import User, Holding, Need, Alert, MarketSignal, FollowUp
from app.dependencies import Actor
from app.services.parser import local_today

def _scope(session: Session, tenant_id: str, owner_id: str):
    session.info.update(tenant_id=tenant_id, user_id=owner_id)

def reconcile_market_signal(factory: sessionmaker, tenant_id: str, signal_id: str,
                            acting_admin_id: str) -> int:
    """Admin lists tenant users; each owner's data is queried using its own RLS scope.

    In the reference app this executes synchronously. Production: queue per-user jobs
    with retries and a transactional outbox.
    """
    with factory() as session:
        _scope(session, tenant_id, acting_admin_id)
        users = session.scalars(select(User.id).where(
            User.tenant_id == tenant_id, User.is_active.is_(True)
        )).all()
        found = session.get(MarketSignal, signal_id)
        if found is None or found.tenant_id != tenant_id:
            return 0
    created = 0
    for user_id in users:
        with factory() as session:
            _scope(session, tenant_id, user_id)
            signal = session.get(MarketSignal, signal_id)
            if signal is None or signal.severity < 3:
                continue
            signal_tags = set(signal.tags)
            holdings = session.scalars(select(Holding).where(
                Holding.tenant_id == tenant_id, Holding.owner_user_id == user_id,
            )).all()
            needs = session.scalars(select(Need).where(
                Need.tenant_id == tenant_id, Need.owner_user_id == user_id,
                Need.status == "open",
            )).all()
            affected: dict[str, list[str]] = {}
            for h in holdings:
                overlap = signal_tags.intersection(h.tags)
                if overlap:
                    affected.setdefault(h.customer_id, []).append(
                        f"销售登记的产品「{h.product_name}」关联主题 {', '.join(sorted(overlap))}"
                    )
            for n in needs:
                overlap = signal_tags.intersection(n.tags)
                if overlap:
                    affected.setdefault(n.customer_id, []).append(
                        f"客户需求「{n.description[:40]}」关联主题 {', '.join(sorted(overlap))}"
                    )
            for customer_id, reasons in affected.items():
                dedup = f"market:{signal.id}:customer:{customer_id}"
                exists = session.scalar(select(Alert.id).where(
                    Alert.tenant_id == tenant_id,
                    Alert.owner_user_id == user_id,
                    Alert.dedup_key == dedup,
                ))
                if exists:
                    continue
                session.add(Alert(
                    tenant_id=tenant_id, owner_user_id=user_id,
                    customer_id=customer_id, signal_id=signal.id,
                    kind="market_attention", dedup_key=dedup,
                    reason_md=(
                        f"### 待人工复核的客户服务提醒\n\n"
                        f"市场事件：{signal.headline}\n\n"
                        f"匹配依据：{'；'.join(reasons)}\n\n"
                        f"事件来源：{signal.evidence_url or '人工录入，未提供外部证据链接'}\n\n"
                        "注：持仓为销售口述记录、并非交易确认；"
                        "本提醒不包含买入、卖出、赎回或收益判断。"
                    ),
                ))
                created += 1
            session.commit()
    return created

def reconcile_due_tasks(db: Session, actor: Actor, until: date | None = None) -> int:
    until = until or local_today()
    tasks = db.scalars(select(FollowUp).where(
        FollowUp.tenant_id == actor.tenant_id,
        FollowUp.owner_user_id == actor.user_id,
        FollowUp.status == "open", FollowUp.due_on <= until,
    )).all()
    created = 0
    for t in tasks:
        key = f"followup:{t.id}:{t.due_on.isoformat()}"
        exists = db.scalar(select(Alert.id).where(
            Alert.tenant_id == actor.tenant_id,
            Alert.owner_user_id == actor.user_id,
            Alert.dedup_key == key,
        ))
        if exists:
            continue
        db.add(Alert(
            tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
            customer_id=t.customer_id, task_id=t.id, kind="followup_due",
            dedup_key=key,
            reason_md=f"### 客户回访待办\n\n到期日期：{t.due_on}\n\n{t.reason_md}",
        ))
        created += 1
    db.commit()
    return created
