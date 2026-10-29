"""会议用例（PR4 占位）：会议建档与素材关联；转写/纪要生成待接入 Transcriber 端口。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies import Actor, get_customer, get_contact
from app.models import Meeting, MediaAsset, AuditLog, uid


def create_meeting(db: Session, actor: Actor, customer_id: str, title: str,
                   occurred_on, contact_id: str | None = None,
                   asset_ids: list[str] | None = None) -> Meeting:
    get_customer(db, actor, customer_id)
    if contact_id:
        get_contact(db, actor, customer_id, contact_id)
    meeting = Meeting(
        id=uid(), tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
        customer_id=customer_id, contact_id=contact_id,
        title=title.strip()[:200], occurred_on=occurred_on,
    )
    db.add(meeting)
    db.flush()
    linked = 0
    for asset_id in (asset_ids or [])[:50]:
        asset = db.scalar(select(MediaAsset).where(
            MediaAsset.id == asset_id,
            MediaAsset.tenant_id == actor.tenant_id,
            MediaAsset.owner_user_id == actor.user_id,
        ))
        if asset and asset.customer_id == customer_id and asset.state == "ready":
            asset.meeting_id = meeting.id
            linked += 1
    db.add(AuditLog(tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                    action="create", resource_type="meeting", resource_id=meeting.id,
                    details_hash=str(hash(title))))
    db.commit()
    meeting.linked_assets = linked  # type: ignore[attr-defined]
    return meeting


def get_meeting(db: Session, actor: Actor, meeting_id: str) -> dict:
    meeting = db.scalar(select(Meeting).where(
        Meeting.id == meeting_id,
        Meeting.tenant_id == actor.tenant_id,
        Meeting.owner_user_id == actor.user_id,
    ))
    if meeting is None:
        raise HTTPException(404, "会议不存在")
    assets = db.scalars(select(MediaAsset).where(
        MediaAsset.meeting_id == meeting.id,
        MediaAsset.tenant_id == actor.tenant_id,
        MediaAsset.owner_user_id == actor.user_id,
    )).all()
    return {
        "id": meeting.id, "customer_id": meeting.customer_id,
        "contact_id": meeting.contact_id, "title": meeting.title,
        "occurred_on": meeting.occurred_on.isoformat(),
        "minutes_status": meeting.minutes_status,
        "minutes_md": meeting.minutes_md,
        "assets": [{"id": a.id, "filename": a.filename, "state": a.state,
                    "content_type": a.content_type} for a in assets],
    }


def list_meetings(db: Session, actor: Actor, customer_id: str) -> list[dict]:
    get_customer(db, actor, customer_id)
    rows = db.scalars(select(Meeting).where(
        Meeting.tenant_id == actor.tenant_id,
        Meeting.owner_user_id == actor.user_id,
        Meeting.customer_id == customer_id,
    ).order_by(Meeting.occurred_on.desc()).limit(100)).all()
    return [{"id": m.id, "title": m.title, "occurred_on": m.occurred_on.isoformat(),
             "minutes_status": m.minutes_status} for m in rows]


def extract_draft_placeholder(db: Session, actor: Actor, meeting_id: str) -> dict:
    """PR4 占位：转写与 AI 纪要属后续接入（app/ai/ports.Transcriber / Summarizer）。

    明确返回 not_implemented，绝不伪造转写内容，也不产生任何业务写入。
    """
    get_meeting(db, actor, meeting_id)
    return {
        "status": "not_implemented",
        "message": ("会议转写与纪要生成属于 PR4：待接入受控 ASR Provider "
                    "（app/ai/ports.Transcriber / Summarizer）与人工校对流程。"
                    "当前版本不生成任何草稿，也不写入需求/任务。"),
        "roadmap": "docs/05_架构演进设计.md §5 / §11 PR4",
    }
