from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.deps import current_user
from app.schemas.common import to_api
from app.services.ownership import not_found, parse_object_id

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobOut(BaseModel):
    id: str
    user_id: str
    brand_id: str
    project_id: str | None = None
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
async def list_jobs(user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)):
    jobs = await db.ai_jobs.find({"user_id": user["_id"]}).sort("created_at", -1).to_list()
    return to_api(jobs)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)):
    job = await db.ai_jobs.find_one({"_id": parse_object_id(job_id), "user_id": user["_id"]})
    if job is None:
        raise not_found()
    return to_api(job)
