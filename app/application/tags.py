"""标签字典用例：用户自管理的主题标签（观点与客户共用），并供自然语言解析做别名映射。"""
from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.text_rules import TAG_ALIASES
from app.dependencies import Actor
from app.models import TagDef, uid

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


def _defaults() -> list[dict]:
    """由内置别名表反推默认标签（label=主词，aliases=同义词）。"""
    by_key: dict[str, dict] = {}
    for word, key in TAG_ALIASES.items():
        item = by_key.setdefault(key, {"key": key, "label": word, "aliases": []})
        if word != item["label"]:
            item["aliases"].append(word)
    return list(by_key.values())


def ensure_defaults(db: Session, actor: Actor) -> None:
    if db.scalar(select(TagDef.id).where(
            TagDef.tenant_id == actor.tenant_id,
            TagDef.owner_user_id == actor.user_id)):
        return
    for item in _defaults():
        db.add(TagDef(id=uid(), tenant_id=actor.tenant_id,
                      owner_user_id=actor.user_id, key=item["key"],
                      label=item["label"], aliases=item["aliases"]))
    db.commit()


def list_tags(db: Session, actor: Actor) -> list[dict]:
    ensure_defaults(db, actor)
    rows = db.scalars(select(TagDef).where(
        TagDef.tenant_id == actor.tenant_id,
        TagDef.owner_user_id == actor.user_id,
    ).order_by(TagDef.key.asc())).all()
    return [{"id": t.id, "key": t.key, "label": t.label, "aliases": t.aliases}
            for t in rows]


def create_tag(db: Session, actor: Actor, key: str, label: str,
               aliases: list[str]) -> dict:
    key = (key or "").strip().lower()
    label = label.strip()
    if not KEY_PATTERN.match(key):
        raise HTTPException(422, "标签键须为小写字母开头的 a-z0-9_（2~40 位），如 mid_cap")
    if not label:
        raise HTTPException(422, "请填写标签显示名")
    if db.scalar(select(TagDef.id).where(
            TagDef.tenant_id == actor.tenant_id,
            TagDef.owner_user_id == actor.user_id,
            TagDef.key == key)):
        raise HTTPException(409, f"标签键 {key} 已存在")
    tag = TagDef(id=uid(), tenant_id=actor.tenant_id,
                 owner_user_id=actor.user_id, key=key, label=label,
                 aliases=[a.strip() for a in aliases if a.strip()][:10])
    db.add(tag)
    db.commit()
    return {"id": tag.id, "key": tag.key, "label": tag.label, "aliases": tag.aliases}


def update_tag(db: Session, actor: Actor, tag_id: str, label: str | None = None,
               aliases: list[str] | None = None) -> dict:
    tag = db.scalar(select(TagDef).where(
        TagDef.id == tag_id, TagDef.tenant_id == actor.tenant_id,
        TagDef.owner_user_id == actor.user_id))
    if tag is None:
        raise HTTPException(404, "标签不存在")
    if label is not None and label.strip():
        tag.label = label.strip()
    if aliases is not None:
        tag.aliases = [a.strip() for a in aliases if a.strip()][:10]
    db.commit()
    return {"id": tag.id, "key": tag.key, "label": tag.label, "aliases": tag.aliases}


def delete_tag(db: Session, actor: Actor, tag_id: str) -> dict:
    tag = db.scalar(select(TagDef).where(
        TagDef.id == tag_id, TagDef.tenant_id == actor.tenant_id,
        TagDef.owner_user_id == actor.user_id))
    if tag is None:
        raise HTTPException(404, "标签不存在")
    db.delete(tag)
    db.commit()
    return {"id": tag_id, "deleted": True}


def alias_map(db: Session, actor: Actor) -> dict[str, str]:
    """用户字典覆盖内置映射：自定义标签/别名立即可用于自然语言解析。"""
    mapping = dict(TAG_ALIASES)
    rows = db.scalars(select(TagDef).where(
        TagDef.tenant_id == actor.tenant_id,
        TagDef.owner_user_id == actor.user_id)).all()
    for t in rows:
        mapping[t.label] = t.key
        for a in t.aliases or []:
            mapping[a] = t.key
    return mapping
