from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from app.deps import Once, Page, Workspace
from app.schemas.common import to_api
from app.schemas.plan import ItemKey, PlanBatch, PlanBatchResult, PlanItemOut, PlanSave, PlanVersionOut
from app.services import plan as service

router = APIRouter(prefix="/projects/{project_id}/plan", tags=["plan"])


@router.get("", response_model=list[PlanItemOut])
async def list_plan(ws: Workspace = Depends()):
    return to_api(await service.list_items(ws.db, ws.project_id))


@router.get("/export")
async def export_plan(format: Literal["md", "txt"] = "md", ws: Workspace = Depends()):
    text = service.export_text(ws.project, await service.list_items(ws.db, ws.project_id))
    media_type = "text/markdown" if format == "md" else "text/plain"
    file_name = quote(f"{ws.project.get('name') or 'project'}-plan.{format}")
    return PlainTextResponse(
        text,
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{file_name}"},
    )


@router.post("/batch", response_model=list[PlanBatchResult])
async def save_plan_batch(body: PlanBatch, once: Once = Depends(), ws: Workspace = Depends()):
    # 항목별 결과를 돌려주므로 일부가 거부되어도 응답은 200이다.
    return to_api(await once.run(lambda: service.save_batch(ws.db, ws.user_id, ws.project_id, body.items)))


@router.get("/{item_key}", response_model=PlanItemOut)
async def get_plan_item(item_key: ItemKey, ws: Workspace = Depends()):
    return to_api(await service.get_item(ws.db, ws.project_id, item_key))


@router.put("/{item_key}", response_model=PlanItemOut)
async def save_plan_item(item_key: ItemKey, body: PlanSave, once: Once = Depends(), ws: Workspace = Depends()):
    return to_api(
        await once.run(
            lambda: service.save_item(
                ws.db, ws.user_id, ws.project_id, item_key, body.kind, body.content, body.amount, body.revision
            )
        )
    )


@router.delete("/{item_key}", response_model=PlanItemOut)
async def clear_plan_item(
    item_key: ItemKey, revision: int = Query(ge=0), once: Once = Depends(), ws: Workspace = Depends()
):
    return to_api(await once.run(lambda: service.clear_item(ws.db, ws.project_id, item_key, revision)))


@router.delete("/{item_key}/draft", response_model=PlanItemOut)
async def discard_plan_draft(item_key: ItemKey, revision: int = Query(ge=0), ws: Workspace = Depends()):
    return to_api(await service.discard_draft(ws.db, ws.project_id, item_key, revision))


@router.get("/{item_key}/versions", response_model=list[PlanVersionOut])
async def list_plan_versions(item_key: ItemKey, page: Page = Depends(), ws: Workspace = Depends()):
    return to_api(await service.list_versions(ws.db, ws.project_id, item_key, page))
