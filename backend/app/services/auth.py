import asyncio
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Response

from app.config import get_settings
from app.errors import AppError

COOKIE_NAME = "access_token"
TOKEN_LIFETIME = timedelta(days=7)
JWT_ALGORITHM = "HS256"

_hasher = PasswordHasher()
# 없는 이메일도 같은 시간이 걸리도록 검증에 쓰는 해시
_DUMMY_HASH = _hasher.hash("dummy-password")


def _jwt_secret() -> str:
    secret = get_settings().jwt_secret
    if not secret:
        raise AppError(503, "AUTH_NOT_CONFIGURED", "인증 설정이 되어 있지 않습니다.")
    return secret


def require_auth_configured() -> None:
    _jwt_secret()


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        await asyncio.to_thread(_hasher.verify, password_hash or _DUMMY_HASH, password)
    except (Argon2Error, InvalidHashError):
        return False
    return password_hash is not None


def create_token(user_id: ObjectId) -> str:
    payload = {"sub": str(user_id), "exp": datetime.now(timezone.utc) + TOKEN_LIFETIME}
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> ObjectId | None:
    try:
        payload = jwt.decode(
            token, _jwt_secret(), algorithms=[JWT_ALGORITHM], options={"require": ["exp", "sub"]}
        )
        return ObjectId(payload["sub"])
    except (jwt.PyJWTError, InvalidId, TypeError):
        return None


def set_auth_cookie(response: Response, user_id: ObjectId) -> None:
    response.set_cookie(
        COOKIE_NAME,
        create_token(user_id),
        max_age=int(TOKEN_LIFETIME.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="lax", secure=get_settings().cookie_secure)
