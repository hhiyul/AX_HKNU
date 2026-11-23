"""할 일(일정) API. 문서 항목에서 만든 일정은 source로 원래 항목에 돌아간다."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.deps import Workspace
from app.schemas import tasks as schema
from app.schemas.common import to_api
from app.services import tasks

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])


@router.get("", response_model=list[schema.TaskOut])
async def list_tasks(status: str = Query("active", pattern="^(active|trash)$"), ws: Workspace = Depends()):
    return to_api(await tasks.list_tasks(ws.db, ws.project_id, status))


@router.post("", response_model=schema.TaskOut, status_code=201)
async def create_task(body: schema.TaskCreate, ws: Workspace = Depends()):
    """일정 등록. 같은 AI 후보(source_proposal_id)로 이미 등록했으면 그 일정을 200으로 돌려준다(existing: true)."""
    task, created = await tasks.create_task(ws.db, ws.user_id, ws.project_id, body)
    if created:
        return to_api(task)
    return JSONResponse(status_code=200, content=schema.TaskOut(**to_api(task)).model_dump(mode="json"))


@router.patch("/{task_id}", response_model=schema.TaskOut)
async def update_task(task_id: str, body: schema.TaskUpdate, ws: Workspace = Depends()):
    """제목·날짜·메모·완료 변경. 완료해도 문서 항목의 검토 상태는 바뀌지 않는다."""
    # null은 "바꾸지 않음"으로 본다.
    changes = {key: value for key, value in body.model_dump(exclude_unset=True, exclude={"revision"}).items() if value is not None}
    return to_api(await tasks.update_task(ws.db, ws.project_id, task_id, changes, body.revision))


@router.delete("/{task_id}", status_code=204)
async def trash_task(task_id: str, ws: Workspace = Depends()):
    await tasks.set_trashed(ws.db, ws.project_id, task_id, True)


@router.post("/{task_id}/restore", response_model=schema.TaskOut)
async def restore_task(task_id: str, ws: Workspace = Depends()):
    return to_api(await tasks.set_trashed(ws.db, ws.project_id, task_id, False))
