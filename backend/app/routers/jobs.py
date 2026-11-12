from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.deps import Page, current_user
from app.schemas.common import to_api
from app.services.store import not_found, parse_object_id

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobOut(BaseModel):
    id: str
    user_id: str
    # 브랜드 없는 프로젝트의 작업이면 없다.
    brand_id: str | None = None
    project_id: str | None = None
    # 작업이 다루는 기획 항목(ck_01~ck_06). 값은 DB 규칙(init_db.py)이 검사한다.
    # 규칙이 바뀌기 전에 저장된 작업도 조회는 되도록 여기서는 문자열로 둔다.
    job_type: str
    input: dict
    status: Literal["queued", "running", "succeeded", "failed"]
    result: dict | None = None
    error_message: str | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# 작업 생성과 상태 변경은 AI 파이프라인 담당 범위라 조회만 제공한다.
@router.get("", response_model=list[JobOut])
async def list_jobs(
    page: Page = Depends(), user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)
):
    cursor = db.ai_jobs.find({"user_id": user["_id"]}).sort("created_at", -1)
    return to_api(await cursor.skip(page.skip).limit(page.limit).to_list())


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)):
    job = await db.ai_jobs.find_one({"_id": parse_object_id(job_id), "user_id": user["_id"]})
    if job is None:
        raise not_found()
    return to_api(job)
