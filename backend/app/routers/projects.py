from fastapi import APIRouter, Depends, status
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.deps import Page, current_user
from app.schemas.common import to_api
from app.schemas.projects import ProjectCreate, ProjectOut, ProjectUpdate
from app.services import projects as service
from app.services.ownership import get_owned_project

router = APIRouter(prefix="/projects", tags=["projects"])

# TODO(IA v0.1): 프로젝트 개요 화면용 GET /projects/{project_id}/overview 추가 예정.
#   기본 정보, 연결 브랜드, 기획 진행 현황, 최근 작업을 모아서 돌려주고 DB에는 따로 저장하지 않는다.
# TODO(IA v0.1): 경제성(비용 입력·가격 시나리오·계산 결과) API도 이 아래 경로로 추가 예정.


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    brand_id: str | None = None,
    page: Page = Depends(),
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    return to_api(await service.list_projects(db, user["_id"], brand_id, page))


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)
):
    return to_api(await service.create_project(db, user["_id"], body))


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)):
    return to_api(await get_owned_project(db, user["_id"], project_id))


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    return to_api(await service.update_project(db, user["_id"], project_id, body))


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)
):
    await service.delete_project(db, user["_id"], project_id)
