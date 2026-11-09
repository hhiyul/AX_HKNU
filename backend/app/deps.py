from fastapi import Depends, Request
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.errors import AppError
from app.services.auth import COOKIE_NAME, decode_token


async def current_user(request: Request, db: AsyncDatabase = Depends(get_db)) -> dict:
    unauthorized = AppError(401, "UNAUTHORIZED", "로그인이 필요합니다.")
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise unauthorized
    user_id = decode_token(token)
    user = await db.users.find_one({"_id": user_id}) if user_id else None
    if user is None:
        raise unauthorized
    return user
