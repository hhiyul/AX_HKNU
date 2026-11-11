"""프로젝트 작업실 API: 글(기획 항목·추가 기록·산출물), 경제성, AI 채팅, AI 제안, 개요."""
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import PlainTextResponse
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.deps import Once, Page, Workspace, current_user
from app.errors import AppError
from app.schemas import workspace as schema
from app.schemas.common import to_api
from app.services import chat, economics, items, overview, proposals
from app.services.store import get_request

BASE = "/projects/{project_id}"
item_router = APIRouter(prefix=f"{BASE}/items", tags=["items"])
economics_router = APIRouter(prefix=f"{BASE}/economics", tags=["economics"])
message_router = APIRouter(prefix=f"{BASE}/messages", tags=["chat"])
proposal_router = APIRouter(prefix=f"{BASE}/proposals", tags=["proposals"])
overview_router = APIRouter(tags=["overview"])
routers = [item_router, economics_router, message_router, proposal_router, overview_router]

RevisionQuery = Query(ge=0)


# ---------- 글: kind는 plan(기획 항목), record(추가 기록), document(산출물) ----------
# 고정 경로(export)를 {kind}보다 먼저 둔다.


@item_router.get("/export")
async def export_plan(format: Literal["md", "txt"] = "md", ws: Workspace = Depends()):
    """6개 기획 항목을 글로 내려받는다. MD와 TXT의 내용은 같다."""
    text = items.export_text(ws.project, await items.list_items(ws.db, ws.project_id, "plan"))
    media_type = "text/markdown" if format == "md" else "text/plain"
    file_name = quote(f"{ws.project.get('name') or 'project'}-plan.{format}")
    return PlainTextResponse(
        text,
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{file_name}"},
    )


@item_router.post("/batch", response_model=list[schema.BatchResult])
async def save_plan_batch(body: schema.ItemBatch, once: Once = Depends(), ws: Workspace = Depends()):
    # 항목별 결과를 돌려주므로 일부가 거부되어도 응답은 200이다.
    return to_api(await once.run(lambda: items.save_batch(ws.db, ws.user_id, ws.project_id, body.items)))


@item_router.get("/{kind}", response_model=list[schema.ItemOut])
async def list_items(kind: schema.ItemKind, page: Page = Depends(), ws: Workspace = Depends()):
    return to_api(await items.list_items(ws.db, ws.project_id, kind, page))


@item_router.post("/{kind}", response_model=schema.ItemOut, status_code=status.HTTP_201_CREATED)
async def create_item(
    kind: schema.ItemKind, body: schema.ItemCreate, once: Once = Depends(), ws: Workspace = Depends()
):
    return to_api(await once.run(lambda: items.create_item(ws.db, ws.user_id, ws.project_id, kind, body)))


@item_router.post("/{kind}/similar", response_model=list[schema.SimilarOut])
async def find_similar(kind: schema.ItemKind, body: schema.SimilarQuery, ws: Workspace = Depends()):
    return to_api(await items.find_similar(ws.db, ws.project_id, kind, body.title, body.content))


@item_router.get("/{kind}/{key}", response_model=schema.ItemOut)
async def get_item(kind: schema.ItemKind, key: str, ws: Workspace = Depends()):
    return to_api(await items.get_item(ws.db, ws.project_id, kind, key))


@item_router.put("/{kind}/{key}", response_model=schema.ItemOut)
async def save_item(
    kind: schema.ItemKind, key: str, body: schema.ItemSave, once: Once = Depends(), ws: Workspace = Depends()
):
    return to_api(
        await once.run(
            lambda: items.save_item(
                ws.db, ws.user_id, ws.project_id, kind, key, body.state, body.content, body.revision,
                title=body.title, amount=body.amount,
            )
        )
    )


@item_router.delete("/{kind}/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(kind: schema.ItemKind, key: str, revision: int = RevisionQuery, ws: Workspace = Depends()):
    await items.delete_item(ws.db, ws.project_id, kind, key, revision)


@item_router.delete("/{kind}/{key}/draft", response_model=schema.ItemOut)
async def discard_draft(kind: schema.ItemKind, key: str, revision: int = RevisionQuery, ws: Workspace = Depends()):
    return to_api(await items.discard_draft(ws.db, ws.project_id, kind, key, revision))


@item_router.post("/document/{key}/sync", response_model=schema.ItemOut)
async def sync_document(key: str, body: schema.RevisionOnly, ws: Workspace = Depends()):
    return to_api(await items.sync_document(ws.db, ws.project_id, key, body.revision))


@item_router.get("/{kind}/{key}/versions", response_model=list[schema.ItemVersionOut])
async def list_versions(kind: schema.ItemKind, key: str, page: Page = Depends(), ws: Workspace = Depends()):
    return to_api(await items.list_versions(ws.db, ws.project_id, kind, key, page))


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
    scenario_id: str, body: schema.RevisionOnly, once: Once = Depends(), ws: Workspace = Depends()
):
    """고른 가격을 가격·원가(plan/ck_05)의 초안으로 넘긴다. revision은 그 항목의 현재 값이다."""
    return to_api(
        await once.run(
            lambda: economics.adopt_scenario(ws.db, ws.user_id, ws.project_id, scenario_id, body.revision)
        )
    )


# ---------- AI 채팅 ----------
# 채팅 설정(검색, 참고 파일·프로젝트)은 PATCH /projects/{id} 로 바꾼다.


@message_router.get("", response_model=list[schema.MessageOut])
async def list_messages(page: Page = Depends(), ws: Workspace = Depends()):
    return to_api(await chat.list_messages(ws.db, ws.project_id, page))


@message_router.post("", response_model=schema.MessageOut, status_code=status.HTTP_201_CREATED)
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
