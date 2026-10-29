"""本地私有对象存储（开发/演示实现，接口对齐 docs/blueprints/contracts.MediaStore）。

- 文件落在 media_storage/<tenant>/<asset>，不入 git、不走静态路由；
- 读取必须凭 CRM 签发的短时 HMAC 票据（绑定 asset + 过期时间），并再次校验 owner ACL；
- 生产替换为 S3 兼容预签名 URL 时保持同一接口。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from app.config import settings

ROOT = Path(__file__).resolve().parents[1] / "media_storage"
TICKET_TTL_SECONDS = 600


def _secret() -> bytes:
    return settings.jwt_secret_key.encode("utf-8")


class LocalObjectStore:
    def storage_key(self, tenant_id: str, asset_id: str, filename: str) -> str:
        safe_ext = Path(filename).suffix[:16]
        return f"{tenant_id}/{asset_id}{safe_ext}"

    def save_bytes(self, key: str, payload: bytes) -> str:
        target = ROOT / key
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + f".{secrets.token_hex(4)}.part")
        tmp.write_bytes(payload)
        os.replace(tmp, target)  # 原子落盘，避免半写文件被当作已就绪
        return str(target)

    def open_bytes(self, key: str) -> bytes:
        return (ROOT / key).read_bytes()

    def delete(self, key: str) -> None:
        target = ROOT / key
        if target.exists():
            target.unlink()


def issue_access_ticket(asset_id: str, ttl: int = TICKET_TTL_SECONDS) -> str:
    """短时访问票据（S3 预签名语义）：ACL 在签发时校验；票据绑定资产与过期时间。"""
    exp = int(time.time()) + ttl
    body = f"{asset_id}:{exp}"
    sig = hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_access_ticket(token: str, asset_id: str) -> bool:
    try:
        exp_text, sig = token.split(".", 1)
        exp = int(exp_text)
    except (ValueError, AttributeError):
        return False
    if exp < int(time.time()):
        return False
    body = f"{asset_id}:{exp}"
    expected = hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)
