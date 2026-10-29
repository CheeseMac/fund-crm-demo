from datetime import datetime, timedelta, timezone
import jwt
from jwt.exceptions import InvalidTokenError
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import HTTPException, status
from app.config import settings

hasher = PasswordHasher(time_cost=2, memory_cost=32768, parallelism=2)

def hash_password(password: str) -> str:
    return hasher.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    try:
        return hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, ValueError):
        return False

def create_token(user_id: str, tenant_id: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user_id, "tid": tenant_id, "iss": "fund-crm", "aud": "fund-crm-api",
         "iat": now, "exp": now + timedelta(minutes=settings.access_token_minutes)},
        settings.jwt_secret_key, algorithm="HS256",
    )

def decode_token(token: str) -> tuple[str, str]:
    try:
        data = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"],
                          issuer="fund-crm", audience="fund-crm-api")
        uid, tid = data["sub"], data["tid"]
        if not (isinstance(uid, str) and isinstance(tid, str)):
            raise ValueError("missing token subject")
        return uid, tid
    except (InvalidTokenError, ValueError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="登录已失效或凭证不正确")
