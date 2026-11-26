"""AI 채팅 대화 목록 API. 메시지는 GET /projects/{id}/messages?conversation_id=, 보내기는 POST /runs의 conversation_id."""
from fastapi import APIRouter, Depends, Query

from app.deps import Workspace
from app.schemas import conversations as schema
from app.schemas.common import to_api
from app.services import conversations
from app.schemas.ai_scope import ScopeSave
from app.services import reference_scope

router = APIRouter(prefix="/projects/{project_id}/conversations", tags=["chat"])


@router.get("/{conversation_id}/scope")
async def get_scope(conversation_id: str, ws: Workspace = Depends()):
    return await reference_scope.get(ws.db, ws.project_id, conversation_id)


@router.put("/{conversation_id}/scope")
async def save_scope(conversation_id: str, body: ScopeSave, ws: Workspace = Depends()):
    return await reference_scope.save(ws.db, ws.project_id, conversation_id, body)


@router.get("", response_model=list[schema.ConversationOut])
async def list_conversations(status: str = Query("active", pattern="^(active|trash)$"), ws: Workspace = Depends()):
    """최근에 이야기한 대화가 먼저. 예전 채팅 메시지가 있으면 처음 부를 때 "이전 대화"로 묶는다."""
    return to_api(await conversations.list_conversations(ws.db, ws.user_id, ws.project_id, status))


@router.post("", response_model=schema.ConversationOut, status_code=201)
async def create_conversation(body: schema.ConversationCreate, ws: Workspace = Depends()):
    return to_api(await conversations.create(ws.db, ws.user_id, ws.project_id, body.title))


@router.patch("/{conversation_id}", response_model=schema.ConversationOut)
async def rename_conversation(conversation_id: str, body: schema.ConversationUpdate, ws: Workspace = Depends()):
    return to_api(await conversations.rename(ws.db, ws.project_id, conversation_id, body.title, body.revision))


@router.delete("/{conversation_id}", status_code=204)
async def trash_conversation(conversation_id: str, ws: Workspace = Depends()):
    """휴지통으로 옮긴다. 메시지는 남고 복원하면 이어서 쓸 수 있다."""
    await conversations.set_trashed(ws.db, ws.project_id, conversation_id, True)


@router.post("/{conversation_id}/restore", response_model=schema.ConversationOut)
async def restore_conversation(conversation_id: str, ws: Workspace = Depends()):
    return to_api(await conversations.set_trashed(ws.db, ws.project_id, conversation_id, False))
