"""프로젝트 작업실 API: 추가 기록, 경제성, 기획 산출물, AI 채팅, AI 제안, 개요."""
from fastapi import APIRouter, Depends, Query, status
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.deps import Once, Page, Workspace, current_user
from app.errors import AppError
from app.schemas import workspace as schema
from app.schemas.common import to_api
from app.services import chat, documents, economics, overview, proposals, records
from app.services.idempotency import get_request

BASE = "/projects/{project_id}"
record_router = APIRouter(prefix=f"{BASE}/record", tags=["record"])
economics_router = APIRouter(prefix=f"{BASE}/economics", tags=["economics"])
document_router = APIRouter(prefix=f"{BASE}/documents", tags=["documents"])
chat_router = APIRouter(prefix=f"{BASE}/chat", tags=["chat"])
proposal_router = APIRouter(prefix=f"{BASE}/proposals", tags=["proposals"])
overview_router = APIRouter(tags=["overview"])
routers = [record_router, economics_router, document_router, chat_router, proposal_router, overview_router]

RevisionQuery = Query(ge=0)


def _changes(body) -> dict:
    """수정 요청에서 보낸 필드만 꺼낸다. revision은 따로 넘긴다."""
    return body.model_dump(exclude_unset=True, exclude={"revision"})


# ---------- 추가 기록 ----------


@record_router.get("", response_model=schema.RecordOut)
async def get_record(ws: Workspace = Depends()):
    return to_api(await records.get_record(ws.db, ws.project_id))


@record_router.post("/entries", response_model=schema.RecordOut, status_code=status.HTTP_201_CREATED)
async def add_entry(body: schema.EntryCreate, once: Once = Depends(), ws: Workspace = Depends()):
    return to_api(
        await once.run(
            lambda: records.add_entry(
                ws.db, ws.user_id, ws.project_id, body.title, body.content, body.state, body.revision
            )
        )
    )


@record_router.patch("/entries/{entry_id}", response_model=schema.RecordOut)
async def update_entry(entry_id: str, body: schema.EntryUpdate, ws: Workspace = Depends()):
    return to_api(
        await records.update_entry(ws.db, ws.user_id, ws.project_id, entry_id, _changes(body), body.revision)
    )


@record_router.delete("/entries/{entry_id}", response_model=schema.RecordOut)
async def delete_entry(entry_id: str, revision: int = RevisionQuery, ws: Workspace = Depends()):
    return to_api(await records.delete_entry(ws.db, ws.user_id, ws.project_id, entry_id, revision))


@record_router.post("/similar", response_model=list[schema.SimilarOut])
async def find_similar(body: schema.SimilarQuery, ws: Workspace = Depends()):
    return to_api(await records.find_similar(ws.db, ws.project_id, body.title, body.content))


# ---------- 경제성 ----------


@economics_router.get("/costs", response_model=schema.CostSheetOut)
async def get_costs(ws: Workspace = Depends()):
    return to_api(await economics.get_sheet(ws.db, ws.project_id))


@economics_router.put("/costs", response_model=schema.CostSheetOut)
async def save_costs(body: schema.CostSheetSave, once: Once = Depends(), ws: Workspace = Depends()):
    return to_api(
        await once.run(lambda: economics.save_sheet(ws.db, ws.user_id, ws.project_id, body.items, body.revision))
    )


@economics_router.get("/scenarios", response_model=list[schema.ScenarioOut])
async def list_scenarios(page: Page = Depends(), ws: Workspace = Depends()):
    return to_api(await economics.list_scenarios(ws.db, ws.project_id, page))


@economics_router.post("/scenarios", response_model=schema.ScenarioOut, status_code=status.HTTP_201_CREATED)
async def create_scenario(body: schema.ScenarioCreate, once: Once = Depends(), ws: Workspace = Depends()):
    return to_api(
        await once.run(
            lambda: economics.create_scenario(ws.db, ws.user_id, ws.project_id, body.price, body.label)
        )
    )


@economics_router.delete("/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scenario(scenario_id: str, ws: Workspace = Depends()):
    await economics.delete_scenario(ws.db, ws.user_id, ws.project_id, scenario_id)


@economics_router.post("/scenarios/{scenario_id}/adopt", response_model=schema.ScenarioAdoptOut)
async def adopt_scenario(
    scenario_id: str, body: schema.ScenarioAdopt, once: Once = Depends(), ws: Workspace = Depends()
):
    return to_api(
        await once.run(
            lambda: economics.adopt_scenario(ws.db, ws.user_id, ws.project_id, scenario_id, body.revision)
        )
    )


# ---------- 기획 산출물 ----------


@document_router.get("", response_model=list[schema.DocumentOut])
async def list_documents(page: Page = Depends(), ws: Workspace = Depends()):
    return to_api(await documents.list_documents(ws.db, ws.project_id, page))


@document_router.post("", response_model=schema.DocumentOut, status_code=status.HTTP_201_CREATED)
async def create_document(body: schema.DocumentCreate, once: Once = Depends(), ws: Workspace = Depends()):
    return to_api(await once.run(lambda: documents.create_document(ws.db, ws.user_id, ws.project_id, body)))


@document_router.get("/{document_id}", response_model=schema.DocumentOut)
async def get_document(document_id: str, ws: Workspace = Depends()):
    return to_api(await documents.get_document(ws.db, ws.user_id, ws.project_id, document_id))


@document_router.patch("/{document_id}", response_model=schema.DocumentOut)
async def update_document(document_id: str, body: schema.DocumentUpdate, ws: Workspace = Depends()):
    return to_api(
        await documents.update_document(
            ws.db, ws.user_id, ws.project_id, document_id, _changes(body), body.revision
        )
    )


@document_router.post("/{document_id}/finalize", response_model=schema.DocumentOut)
async def finalize_document(
    document_id: str, body: schema.RevisionOnly, once: Once = Depends(), ws: Workspace = Depends()
):
    return to_api(
        await once.run(
            lambda: documents.finalize_document(ws.db, ws.user_id, ws.project_id, document_id, body.revision)
        )
    )


@document_router.post("/{document_id}/sync", response_model=schema.DocumentOut)
async def sync_document(document_id: str, body: schema.RevisionOnly, ws: Workspace = Depends()):
    return to_api(await documents.sync_document(ws.db, ws.user_id, ws.project_id, document_id, body.revision))


@document_router.get("/{document_id}/versions", response_model=list[schema.DocumentVersionOut])
async def list_document_versions(document_id: str, page: Page = Depends(), ws: Workspace = Depends()):
    return to_api(await documents.list_versions(ws.db, ws.user_id, ws.project_id, document_id, page))


@document_router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str, ws: Workspace = Depends()):
    await documents.delete_document(ws.db, ws.user_id, ws.project_id, document_id)


# ---------- AI 채팅 ----------


@chat_router.get("", response_model=schema.ChatOut)
async def get_chat(ws: Workspace = Depends()):
    return to_api(await chat.get_chat(ws.db, ws.user_id, ws.project_id))


@chat_router.patch("", response_model=schema.ChatOut)
async def update_chat(body: schema.ChatUpdate, ws: Workspace = Depends()):
    return to_api(await chat.update_chat(ws.db, ws.user_id, ws.project_id, _changes(body), body.revision))


@chat_router.get("/messages", response_model=list[schema.MessageOut])
async def list_messages(page: Page = Depends(), ws: Workspace = Depends()):
    return to_api(await chat.list_messages(ws.db, ws.user_id, ws.project_id, page))


@chat_router.post("/messages", response_model=schema.MessageOut, status_code=status.HTTP_201_CREATED)
async def add_message(body: schema.MessageCreate, once: Once = Depends(), ws: Workspace = Depends()):
    # 사용자 메시지만 받는다. AI 답변은 파이프라인이 서비스 함수로 남긴다.
    return to_api(
        await once.run(
            lambda: chat.add_message(ws.db, ws.user_id, ws.project_id, "USER", body.content, body.asset_ids)
        )
    )


# ---------- AI 제안 ----------


@proposal_router.get("", response_model=list[schema.ProposalOut])
async def list_proposals(
    state: schema.ProposalState | None = None, page: Page = Depends(), ws: Workspace = Depends()
):
    return to_api(await proposals.list_proposals(ws.db, ws.project_id, state, page))


@proposal_router.post("", response_model=schema.ProposalOut, status_code=status.HTTP_201_CREATED)
async def create_proposal(body: schema.ProposalCreate, once: Once = Depends(), ws: Workspace = Depends()):
    return to_api(
        await once.run(
            lambda: proposals.create_proposal(
                ws.db, ws.user_id, ws.project_id, body.target, body.content, body.message_id
            )
        )
    )


@proposal_router.get("/{proposal_id}", response_model=schema.ProposalOut)
async def get_proposal(proposal_id: str, ws: Workspace = Depends()):
    return to_api(await proposals.get_proposal(ws.db, ws.user_id, ws.project_id, proposal_id))


@proposal_router.post("/{proposal_id}/hold", response_model=schema.ProposalOut)
async def hold_proposal(proposal_id: str, ws: Workspace = Depends()):
    return to_api(await proposals.set_state(ws.db, ws.user_id, ws.project_id, proposal_id, "HELD"))


@proposal_router.post("/{proposal_id}/reject", response_model=schema.ProposalOut)
async def reject_proposal(proposal_id: str, ws: Workspace = Depends()):
    return to_api(await proposals.set_state(ws.db, ws.user_id, ws.project_id, proposal_id, "REJECTED"))


@proposal_router.post("/{proposal_id}/apply", response_model=schema.ProposalOut)
async def apply_proposal(
    proposal_id: str, body: schema.ProposalApply, once: Once = Depends(), ws: Workspace = Depends()
):
    return to_api(
        await once.run(lambda: proposals.apply_proposal(ws.db, ws.user_id, ws.project_id, proposal_id, body))
    )


# ---------- 개요 · 맥락 · 요청 결과 ----------


@overview_router.get(f"{BASE}/overview", response_model=schema.OverviewOut)
async def get_overview(ws: Workspace = Depends()):
    return to_api(await overview.get_overview(ws.db, ws.project))


@overview_router.get(f"{BASE}/projection", response_model=schema.ProjectionOut)
async def get_projection(sections: str | None = None, ws: Workspace = Depends()):
    """sections는 쉼표로 구분한다. 비우면 전체."""
    wanted = [name.strip() for name in sections.split(",") if name.strip()] if sections else overview.SECTIONS
    unknown = [name for name in wanted if name not in overview.SECTIONS]
    if unknown:
        raise AppError(422, "VALIDATION_ERROR", f"입력값을 확인해 주세요: sections ({', '.join(unknown)})")
    return to_api(await overview.get_projection(ws.db, ws.project, wanted))


@overview_router.get("/requests/{key}", response_model=schema.RequestOut)
async def get_request_result(key: str, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)):
    """저장 요청의 응답을 못 받았을 때 반영 여부를 확인한다. 다시 보내기 전에 먼저 조회한다."""
    return to_api(await get_request(db, user["_id"], key))
