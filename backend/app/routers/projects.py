from fastapi import APIRouter, Depends, status
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.deps import Once, Page, current_user
from app.schemas.common import to_api
from app.schemas.projects import ProjectCreate, ProjectOut, ProjectUpdate, Status
from app.services import projects as service
from app.services.store import get_owned_project

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    brand_id: str | None = None,
    status: Status = "active",
    q: str | None = None,
    page: Page = Depends(),
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    """brand_id를 비우면 모든 브랜드, none이면 브랜드 없는 프로젝트만 나온다. q는 이름 검색이다."""
    return to_api(await service.list_projects(db, user["_id"], brand_id, status, q, page))


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreate,
    once: Once = Depends(),
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    return to_api(await once.run(lambda: service.create_project(db, user["_id"], body)))


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)):
    return to_api(service.view(await get_owned_project(db, user["_id"], project_id)))


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    """보관·휴지통·복원도 여기서 한다 (status)."""
    return to_api(await service.update_project(db, user["_id"], project_id, body))


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    permanent: bool = False,
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    """휴지통으로 옮긴다. permanent=true면 휴지통에 있는 프로젝트를 완전히 지운다."""
    await service.delete_project(db, user["_id"], project_id, permanent)
