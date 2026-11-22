"""AI 채팅 실행 API. 메시지를 보내면 실행이 시작되고, 진행 상황은 SSE로 받는다.

실행 상태·스트림·중지는 보관함·휴지통에 있는 프로젝트에서도 쓸 수 있다(시작만 진행 중인 프로젝트에서).
"""
import json

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from pymongo.asynchronous.database import AsyncDatabase

from app.ai.runtime import get_runtime
from app.db import get_db
from app.deps import Workspace, current_user, owned_any_project
from app.schemas import runs as schema
from app.schemas.common import to_api
from app.services import ai_chat, ai_runs

PING_SECONDS = 15
router = APIRouter(prefix="/projects/{project_id}/runs", tags=["ai"])


@router.post("", response_model=schema.RunStarted, status_code=202)
async def start_run(body: schema.RunCreate, ws: Workspace = Depends()):
    """사용자 메시지를 저장하고 AI 답변 만들기를 시작한다. 진행 중인 실행이 있으면 409 RUN_ACTIVE."""
    run_id = await ai_chat.start_run(
        ws.db, get_runtime(), ws.user_id, ws.project, body.text, body.public_search_terms,
        body.mode, body.include_session, body.asset_ids, body.reference_document, body.reference_body,
    )
    return {"run_id": str(run_id)}


@router.get("/{run_id}", response_model=schema.RunOut)
async def get_run(
    run_id: str,
    project: dict = Depends(owned_any_project),
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    return to_api(await ai_runs.get_run(db, user["_id"], project["_id"], run_id))


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: str,
    after: int = 0,
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
    project: dict = Depends(owned_any_project),
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    """after(또는 다시 연결할 때 브라우저가 보내는 Last-Event-ID) 다음 사건부터 보낸다.

    끝났거나 10분이 지난 실행은 남은 사건만 보내고 바로 끝난다. 사건이 없으면 15초마다 ping 주석을 보낸다.
    """
    run = await ai_runs.get_run(db, user["_id"], project["_id"], run_id)
    events = get_runtime().events
    start = max(after, last_event_id or 0, 0)

    async def body():
        async for event in events.subscribe(str(run["_id"]), start, PING_SECONDS):
            if event is None:
                yield ": ping\n\n"
                continue
            yield f"id: {event['seq']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    # nginx가 응답을 모아 두지 않고 바로 흘려보내게 한다.
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(body(), media_type="text/event-stream", headers=headers)


@router.post("/{run_id}/stop", response_model=schema.StopOut)
async def stop_run(
    run_id: str,
    project: dict = Depends(owned_any_project),
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    return {"status": await ai_chat.stop_run(db, get_runtime(), user["_id"], project["_id"], run_id)}
