from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, Header, Query, Request
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.errors import AppError
from app.services.auth import COOKIE_NAME, decode_token
from app.services.store import get_owned_project, run_once


class Page:
    """목록 조회 범위. 한 번에 100개까지 돌려준다."""

    def __init__(self, limit: int = Query(default=50, ge=1, le=100), skip: int = Query(default=0, ge=0)):
        self.limit = limit
        self.skip = skip


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


async def owned_project(
    project_id: str, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)
) -> dict:
    project = await get_owned_project(db, user["_id"], project_id)
    # 작업실은 진행 중인 프로젝트만 연다. 보관함·휴지통의 프로젝트는 먼저 복원해야 한다.
    if project.get("archived_at") or project.get("deleted_at"):
        raise AppError(409, "PROJECT_NOT_ACTIVE", "보관함이나 휴지통에 있는 프로젝트입니다. 먼저 복원해 주세요.")
    return project


class Workspace:
    """프로젝트 하위 API가 공통으로 쓰는 묶음: 로그인 사용자, DB, 내 프로젝트."""

    def __init__(
        self,
        project: dict = Depends(owned_project),
        user: dict = Depends(current_user),
        db: AsyncDatabase = Depends(get_db),
    ):
        self.project = project
        self.project_id = project["_id"]
        self.user_id = user["_id"]
        self.db = db


class Once:
    """Idempotency-Key 헤더가 있으면 같은 키의 저장 요청을 한 번만 처리한다."""

    def __init__(
        self,
        # 경로의 {key}와 겹치지 않게 이름을 길게 둔다.
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=100),
        user: dict = Depends(current_user),
        db: AsyncDatabase = Depends(get_db),
    ):
        self.key = idempotency_key
        self.user_id = user["_id"]
        self.db = db

    async def run(self, make: Callable[[], Awaitable[Any]]) -> Any:
        return await run_once(self.db, self.user_id, self.key, make)
