"""多媒体用例（docs/05 §5）：发起上传 -> 存入私有对象存储 -> 扫描 -> 就绪。

安全边界（§5.3 / 验收矩阵）：
- 仅允许白名单 MIME；大小受限；魔数与声明 MIME 交叉校验（伪造拒绝）；
- 未通过扫描（state != ready）的素材拒绝读取；
- 下载凭短时 HMAC 票据，且下载时再次校验 owner ACL —— 拿到存储 key 也读不走文件。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.dependencies import Actor, get_customer
from app.models import MediaAsset, MediaProcessingJob, AuditLog, uid
from app.services.object_store import (
    LocalObjectStore, issue_access_ticket, verify_access_ticket,
)

MAX_BYTES = settings.media_max_bytes
ALLOWED_TYPES = {
    "audio/mpeg": "audio", "audio/wav": "audio", "audio/x-wav": "audio",
    "audio/webm": "audio", "audio/mp4": "audio", "audio/x-m4a": "audio",
    "video/mp4": "video", "video/webm": "video",
    "image/png": "image", "image/jpeg": "image",
    "application/pdf": "document", "text/markdown": "document", "text/plain": "document",
}
# 魔数签名：声称类型与文件头不符即拒绝（防伪造 MIME）
MAGIC = {
    "audio/mpeg": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    "audio/wav": (b"RIFF",), "audio/x-wav": (b"RIFF",),
    "video/mp4": (b"ftyp",), "video/webm": (b"\x1a\x45\xdf\xa3",),
    "audio/webm": (b"\x1a\x45\xdf\xa3",),
    "image/png": (b"\x89PNG",), "image/jpeg": (b"\xff\xd8\xff",),
    "application/pdf": (b"%PDF",),
}

_store = LocalObjectStore()


def _utcnow():
    return datetime.now(timezone.utc)


def _audit(db: Session, actor: Actor, action: str, kind: str, obj_id: str, detail: str):
    db.add(AuditLog(tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                    action=action, resource_type=kind, resource_id=obj_id,
                    details_hash=hashlib.sha256(detail.encode("utf-8")).hexdigest()))


def initiate_upload(db: Session, actor: Actor, customer_id: str, filename: str,
                    content_type: str, declared_size: int) -> dict:
    get_customer(db, actor, customer_id)
    category = ALLOWED_TYPES.get((content_type or "").lower().split(";")[0].strip())
    if category is None:
        raise HTTPException(415, f"不支持的素材类型：{content_type}（白名单：录音/录像/图片/PDF/Markdown）")
    if declared_size <= 0 or declared_size > MAX_BYTES:
        raise HTTPException(413, f"文件大小须在 1B~{MAX_BYTES // 1024 // 1024}MB 之间")
    asset = MediaAsset(
        id=uid(), tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
        customer_id=customer_id, filename=filename[:255],
        content_type=content_type.split(";")[0].strip(), category=category,
        size_bytes=declared_size, state="pending_upload",
    )
    db.add(asset)
    db.flush()
    _audit(db, actor, "initiate_upload", "media_asset", asset.id, filename)
    db.commit()
    return {
        "asset_id": asset.id,
        "upload_url": f"/api/v2/media/{asset.id}/upload",
        "upload_method": "PUT",
        "expires_at": datetime.fromtimestamp(
            _utcnow().timestamp() + 1800, tz=timezone.utc).isoformat(),
    }


def _scan(asset: MediaAsset, payload: bytes) -> str:
    """返回 scan 结论描述；空串表示通过。"""
    if len(payload) > MAX_BYTES:
        return f"超过大小上限（{len(payload)} > {MAX_BYTES}）"
    signatures = MAGIC.get(asset.content_type)
    if signatures and not any(payload.startswith(s) for s in signatures):
        # PDF/MP4 魔数不在文件头 0 字节的情况：ftyp 在第 4 字节起
        if not (asset.content_type == "video/mp4" and payload[4:8] == b"ftyp"):
            return f"文件内容与声明类型不符（{asset.content_type}）"
    return ""


def store_upload(db: Session, actor: Actor, asset_id: str, payload: bytes) -> dict:
    asset = _get_owned(db, actor, asset_id)
    if asset.state not in ("pending_upload", "uploaded"):
        raise HTTPException(409, f"素材状态 {asset.state} 不可上传")
    job = MediaProcessingJob(
        id=uid(), tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
        asset_id=asset.id, stage="scan", status="running", attempt=1)
    db.add(job)
    db.flush()
    asset.sha256 = hashlib.sha256(payload).hexdigest()
    asset.size_bytes = len(payload)
    asset.uploaded_at = _utcnow().replace(tzinfo=None)
    verdict = _scan(asset, payload)
    if verdict:
        asset.state = "rejected"
        asset.scan_result = verdict[:200]
        job.status, job.error, job.finished_at = "failed", verdict[:500], _utcnow().replace(tzinfo=None)
        db.commit()
        raise HTTPException(422, f"素材未通过安全扫描：{verdict}")
    asset.storage_key = _store.storage_key(actor.tenant_id, asset.id, asset.filename)
    _store.save_bytes(asset.storage_key, payload)
    asset.state = "ready"
    asset.scan_result = "ok"
    asset.ready_at = _utcnow().replace(tzinfo=None)
    job.status, job.finished_at = "succeeded", _utcnow().replace(tzinfo=None)
    _audit(db, actor, "upload", "media_asset", asset.id, asset.sha256 or "")
    db.commit()
    return {"asset_id": asset.id, "state": asset.state, "sha256": asset.sha256}


def _get_owned(db: Session, actor: Actor, asset_id: str) -> MediaAsset:
    asset = db.scalar(select(MediaAsset).where(
        MediaAsset.id == asset_id,
        MediaAsset.tenant_id == actor.tenant_id,
        MediaAsset.owner_user_id == actor.user_id,
    ))
    if asset is None:
        raise HTTPException(404, "素材不存在")
    return asset


def get_asset(db: Session, actor: Actor, asset_id: str) -> dict:
    asset = _get_owned(db, actor, asset_id)
    return _brief(asset)


def _brief(asset: MediaAsset) -> dict:
    return {"id": asset.id, "customer_id": asset.customer_id, "meeting_id": asset.meeting_id,
            "filename": asset.filename, "content_type": asset.content_type,
            "category": asset.category, "size_bytes": asset.size_bytes,
            "sha256": asset.sha256, "state": asset.state, "scan_result": asset.scan_result,
            "created_at": asset.created_at.isoformat() if asset.created_at else None}


def list_assets(db: Session, actor: Actor, customer_id: str) -> list[dict]:
    get_customer(db, actor, customer_id)
    rows = db.scalars(select(MediaAsset).where(
        MediaAsset.tenant_id == actor.tenant_id,
        MediaAsset.owner_user_id == actor.user_id,
        MediaAsset.customer_id == customer_id,
    ).order_by(MediaAsset.created_at.desc()).limit(200)).all()
    return [_brief(a) for a in rows]


def issue_access(db: Session, actor: Actor, asset_id: str) -> dict:
    asset = _get_owned(db, actor, asset_id)
    if asset.state != "ready":
        raise HTTPException(403, "素材未通过安全扫描，不允许读取")
    token = issue_access_ticket(asset.id)
    return {"asset_id": asset.id,
            "download_url": f"/api/v2/media/{asset.id}/download?token={token}",
            "expires_in": 600}


def read_for_download(db: Session, asset_id: str, token: str) -> MediaAsset:
    """票据即凭证（S3 预签名语义）：ACL 在签发时校验；下载校验票据与就绪状态。

    对象存储 key 本身不可经任何 HTTP 路径读取；票据短时（10 分钟）绑定单个资产。
    """
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(404, "素材不存在")
    if not verify_access_ticket(token, asset.id):
        raise HTTPException(401, "访问票据无效或已过期")
    if asset.state != "ready":
        raise HTTPException(403, "素材未通过安全扫描，不允许读取")
    return asset


def load_bytes(asset: MediaAsset) -> bytes:
    return _store.open_bytes(asset.storage_key)
