"""市场观点用例：指定日期的观点留痕（谁说的、说了什么、挂什么标签）。

观点只是记录与个人参考，不是市场事件：不触发提醒、不自动推送；
「关联客户」是只读的标签交集视图（客户需求/持仓与观点共用同一套标签）。
"""
from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies import Actor
from app.models import MarketViewpoint, Customer, Holding, Need, uid

AUTHOR_TYPES = {"self", "colleague", "external", "institution"}


def create_viewpoint(db: Session, actor: Actor, viewpoint_date: date,
                     author_type: str, author_name: str, content_md: str,
                     tags: list[str], source_note: str = "") -> MarketViewpoint:
    if author_type not in AUTHOR_TYPES:
        raise HTTPException(422, f"作者类型仅允许 {'/'.join(sorted(AUTHOR_TYPES))}")
    content_md = (content_md or "").strip()
    if len(content_md) < 2:
        raise HTTPException(422, "请填写观点内容")
    viewpoint = MarketViewpoint(
        id=uid(), tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
        viewpoint_date=viewpoint_date, author_type=author_type,
        author_name=(author_name.strip() or "本人")[:80],
        content_md=content_md[:5000], tags=[t for t in tags if t][:10],
        source_note=(source_note or "").strip()[:200],
    )
    db.add(viewpoint)
    db.commit()
    return viewpoint


def _brief(v: MarketViewpoint) -> dict:
    return {"id": v.id, "viewpoint_date": v.viewpoint_date.isoformat(),
            "author_type": v.author_type, "author_name": v.author_name,
            "content_md": v.content_md, "tags": v.tags, "source_note": v.source_note,
            "created_at": v.created_at.isoformat() if v.created_at else None}


def list_viewpoints(db: Session, actor: Actor, tag: str | None = None,
                    date_from: date | None = None, date_to: date | None = None,
                    limit: int = 100) -> list[dict]:
    q = select(MarketViewpoint).where(
        MarketViewpoint.tenant_id == actor.tenant_id,
        MarketViewpoint.owner_user_id == actor.user_id)
    if tag:
        q = q.where(MarketViewpoint.tags.contains([tag]))
    if date_from:
        q = q.where(MarketViewpoint.viewpoint_date >= date_from)
    if date_to:
        q = q.where(MarketViewpoint.viewpoint_date <= date_to)
    rows = db.scalars(q.order_by(MarketViewpoint.viewpoint_date.desc(),
                                 MarketViewpoint.created_at.desc()).limit(limit)).all()
    return [_brief(v) for v in rows]


def _get_owned(db: Session, actor: Actor, viewpoint_id: str) -> MarketViewpoint:
    v = db.scalar(select(MarketViewpoint).where(
        MarketViewpoint.id == viewpoint_id,
        MarketViewpoint.tenant_id == actor.tenant_id,
        MarketViewpoint.owner_user_id == actor.user_id))
    if v is None:
        raise HTTPException(404, "观点不存在")
    return v


def update_viewpoint(db: Session, actor: Actor, viewpoint_id: str,
                     viewpoint_date: date | None = None, author_type: str | None = None,
                     author_name: str | None = None, content_md: str | None = None,
                     tags: list[str] | None = None,
                     source_note: str | None = None) -> dict:
    v = _get_owned(db, actor, viewpoint_id)
    if viewpoint_date is not None:
        v.viewpoint_date = viewpoint_date
    if author_type is not None:
        if author_type not in AUTHOR_TYPES:
            raise HTTPException(422, "非法作者类型")
        v.author_type = author_type
    if author_name is not None:
        v.author_name = author_name.strip()[:80] or "本人"
    if content_md is not None and content_md.strip():
        v.content_md = content_md.strip()[:5000]
    if tags is not None:
        v.tags = [t for t in tags if t][:10]
    if source_note is not None:
        v.source_note = source_note.strip()[:200]
    db.commit()
    return _brief(v)


def delete_viewpoint(db: Session, actor: Actor, viewpoint_id: str) -> dict:
    v = _get_owned(db, actor, viewpoint_id)
    db.delete(v)
    db.commit()
    return {"id": viewpoint_id, "deleted": True}


def matched_customers(db: Session, actor: Actor, viewpoint_id: str) -> dict:
    """只读标签交集：观点标签 ∩ 我的客户开放需求/持仓申报标签。"""
    v = _get_owned(db, actor, viewpoint_id)
    viewpoint_tags = set(v.tags)
    customers = db.scalars(select(Customer).where(
        Customer.tenant_id == actor.tenant_id,
        Customer.owner_user_id == actor.user_id)).all()
    name_by_id = {c.id: c.name for c in customers}
    matches: dict[str, list[str]] = {}
    holdings = db.scalars(select(Holding).where(
        Holding.tenant_id == actor.tenant_id,
        Holding.owner_user_id == actor.user_id)).all()
    needs = db.scalars(select(Need).where(
        Need.tenant_id == actor.tenant_id,
        Need.owner_user_id == actor.user_id,
        Need.status == "open")).all()
    for h in holdings:
        overlap = viewpoint_tags.intersection(h.tags or [])
        if overlap and h.customer_id in name_by_id:
            matches.setdefault(h.customer_id, []).append(
                f"持仓「{h.product_name}」标签 {', '.join(sorted(overlap))}")
    for n in needs:
        overlap = viewpoint_tags.intersection(n.tags or [])
        if overlap and n.customer_id in name_by_id:
            matches.setdefault(n.customer_id, []).append(
                f"需求「{n.description[:30]}」标签 {', '.join(sorted(overlap))}")
    return {"viewpoint_id": v.id, "tags": sorted(viewpoint_tags),
            "customers": [{"customer_id": cid, "name": name_by_id[cid],
                           "reasons": reasons}
                          for cid, reasons in sorted(matches.items())]}
