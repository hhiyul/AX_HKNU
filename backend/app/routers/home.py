"""계정 홈 API."""
import datetime as dt

from fastapi import APIRouter, Depends, Query
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.deps import current_user
from app.schemas.common import to_api
from app.services import home, tasks

router = APIRouter(prefix="/home", tags=["home"])
task_router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
async def get_home(
    brand_id: str | None = None,
    today: dt.date | None = None,
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    """brand_id를 주면 그 브랜드(none이면 브랜드 없는 프로젝트)만. today(YYYY-MM-DD)를 비우면 한국 날짜."""
    return to_api(await home.get_home(db, user["_id"], brand_id, today or home.korean_today()))


@task_router.get("")
async def list_account_tasks(
    brand_id: str | None = None,
    start: dt.date | None = Query(None, alias="from"),
    end: dt.date | None = Query(None, alias="to"),
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    """계정 전체 일정(전체 일정·달력). from·to(YYYY-MM-DD)로 기간을 고른다. 각 일정의 source로 원래 항목에 돌아간다."""
    projects = await home.active_projects(db, user["_id"], brand_id)
    return to_api(await tasks.list_account_tasks(
        db, projects, start.isoformat() if start else None, end.isoformat() if end else None
    ))
