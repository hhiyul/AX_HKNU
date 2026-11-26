"""사용자 참고 선택/원본 확인. 모델도 같은 SourceReader를 사용한다."""
from fastapi import APIRouter, Depends, Query

from app.deps import Workspace
from app.services import reference_scope, item_context
from app.services.source_reader import SourceReader

router = APIRouter(prefix="/projects/{project_id}/ai-sources", tags=["ai"])


async def reader(ws, conversation_id):
    scope = (await reference_scope.get(ws.db, ws.project_id, conversation_id))["scope"] if conversation_id else reference_scope.normalize(None)
    return SourceReader(ws.db, ws.project, ws.user_id, scope)


@router.get("")
async def search(query: str = Query("", max_length=500), offset: int = Query(0, ge=0, le=1000000),
                 conversation_id: str | None = None, ws: Workspace = Depends()):
    return await (await reader(ws, conversation_id)).search(query, offset=offset)


@router.get("/read")
async def read(key: str = Query(..., max_length=200), offset: int = Query(0, ge=0, le=1000000),
               conversation_id: str | None = None, doc_id: str | None = None, section_id: str | None = None, ws: Workspace = Depends()):
    selected = await reader(ws, conversation_id)
    if doc_id and section_id:
        context = await item_context.get(ws.db, ws.project_id, doc_id, section_id)
        scope = {**context["scope"], "_target_doc_id": doc_id}
        scope["message_ids"] = list(dict.fromkeys([*scope["message_ids"], *(r["id"] for r in context["references"] if r["type"] == "message")]))
        selected = SourceReader(ws.db, ws.project, ws.user_id, scope)
    return await selected.read(key, offset)
