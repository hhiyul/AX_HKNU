"""프로젝트 작업실 API: 글(기획 항목·AI 정리 내용·산출물), 경제성, AI 채팅, 개요."""
from fastapi import APIRouter, Depends, Query
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.deps import Once, Page, Workspace, current_user
from app.errors import AppError
from app.schemas import workspace as schema
from app.schemas.common import to_api
from app.services import chat, economics, items, overview
from app.services.store import get_request

BASE = "/projects/{project_id}"
item_router = APIRouter(prefix=f"{BASE}/items", tags=["items"])
economics_router = APIRouter(prefix=f"{BASE}/economics", tags=["economics"])
message_router = APIRouter(prefix=f"{BASE}/messages", tags=["chat"])
overview_router = APIRouter(tags=["overview"])
routers = [item_router, economics_router, message_router, overview_router]

RevisionQuery = Query(ge=0)


# ---------- 글: kind는 plan(기획 항목), record(AI와 함께 정리한 내용), document(산출물) ----------


@item_router.get("/{kind}", response_model=list[schema.ItemOut])
async def list_items(kind: schema.ItemKind, page: Page = Depends(), ws: Workspace = Depends()):
    return to_api(await items.list_items(ws.db, ws.project_id, kind, page))


@item_router.post("/{kind}", response_model=schema.ItemOut, status_code=201)
async def create_item(
    kind: schema.ItemKind, body: schema.ItemCreate, once: Once = Depends(), ws: Workspace = Depends()
):
    """AI 답변을 검수해서 저장하거나(record), 산출물로 저장한다(document)."""
    return to_api(await once.run(lambda: items.create_item(ws.db, ws.user_id, ws.project_id, kind, body)))


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
                ws.db, ws.user_id, ws.project_id, kind, key, body.revision,
                content=body.content, title=body.title, amount=body.amount,
                as_draft=body.as_draft,
            )
        )
    )


@item_router.delete("/{kind}/{key}", status_code=204)
async def delete_item(kind: schema.ItemKind, key: str, revision: int = RevisionQuery, ws: Workspace = Depends()):
    await items.delete_item(ws.db, ws.project_id, kind, key, revision)


@item_router.delete("/document/{key}/draft", response_model=schema.ItemOut)
async def discard_draft(key: str, revision: int = RevisionQuery, ws: Workspace = Depends()):
    return to_api(await items.discard_draft(ws.db, ws.project_id, key, revision))


# ---------- 경제성 ----------


@economics_router.get("", response_model=schema.EconomicsOut)
async def get_economics(ws: Workspace = Depends()):
    """입력값과 함께 변경 전·가격 유지·가격 변경 비교를 계산해서 돌려준다."""
    return to_api(await economics.get_economics(ws.db, ws.project_id))


@economics_router.put("", response_model=schema.EconomicsOut)
async def save_costs(body: schema.EconomicsSave, once: Once = Depends(), ws: Workspace = Depends()):
    return to_api(
        await once.run(
            lambda: economics.save_costs(ws.db, ws.user_id, ws.project_id, body.cost.model_dump(), body.revision)
        )
    )


@economics_router.post("/baseline", response_model=schema.EconomicsOut)
async def save_baseline(body: schema.RevisionOnly, ws: Workspace = Depends()):
    """현재 입력을 변경 전 비교 기준으로 저장한다."""
    return to_api(await economics.save_baseline(ws.db, ws.project_id, body.revision))


@economics_router.post("/confirm", response_model=schema.EconomicsOut)
async def confirm_price(body: schema.PriceConfirm, once: Once = Depends(), ws: Workspace = Depends()):
    """고른 판매가를 상품기획의 가격결정(plan/ck_05)에 반영한다."""
    return to_api(
        await once.run(
            lambda: economics.confirm_price(ws.db, ws.user_id, ws.project_id, body.mode, body.revision)
        )
    )


# ---------- AI 채팅 ----------
# 채팅 설정(검색, 참고 파일·프로젝트)은 PATCH /projects/{id} 로 바꾼다.
# 답변 저장은 POST /projects/{id}/items/record 또는 /items/document 에 message_id를 넣어 보낸다.


@message_router.get("", response_model=list[schema.MessageOut])
async def list_messages(page: Page = Depends(), ws: Workspace = Depends()):
    return to_api(await chat.list_messages(ws.db, ws.project_id, page))


@message_router.post("", response_model=schema.MessageOut, status_code=201)
async def add_message(body: schema.MessageCreate, once: Once = Depends(), ws: Workspace = Depends()):
    # 사용자 메시지만 받는다. AI 답변은 파이프라인이 서비스 함수로 남긴다.
    return to_api(
        await once.run(
            lambda: chat.add_message(ws.db, ws.user_id, ws.project_id, "USER", body.content, body.asset_ids)
        )
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
