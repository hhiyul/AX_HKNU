from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response, status
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.db import get_db
from app.deps import current_user
from app.errors import AppError
from app.schemas.auth import LoginRequest, SignupRequest, UserOut
from app.schemas.common import to_api
from app.services import rate_limit
from app.services.auth import (
    clear_auth_cookie,
    hash_password,
    require_auth_configured,
    set_auth_cookie,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, response: Response, db: AsyncDatabase = Depends(get_db)):
    # 계정만 만들어지고 로그인이 실패하는 일이 없도록 먼저 확인
    require_auth_configured()
    now = datetime.now(timezone.utc)
    user = {
        "email": body.email,
        "password_hash": await hash_password(body.password),
        "creator_name": body.creator_name,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db.users.insert_one(user)
    except DuplicateKeyError:
        raise AppError(409, "EMAIL_TAKEN", "이미 가입된 이메일입니다.") from None
    set_auth_cookie(response, user["_id"])
    return to_api(user)


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncDatabase = Depends(get_db)):
    require_auth_configured()
    address = rate_limit.client_address(request)
    rate_limit.check_login(body.email, address)
    user = await db.users.find_one({"email": body.email})
    if not await verify_password(body.password, user["password_hash"] if user else None):
        rate_limit.login_failed(body.email, address)
        raise AppError(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다.")
    rate_limit.login_succeeded(body.email)
    set_auth_cookie(response, user["_id"])
    return to_api(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    clear_auth_cookie(response)


@router.get("/me", response_model=UserOut)
async def me(user: dict = Depends(current_user)):
    return to_api(user)
