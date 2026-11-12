"""경제성. 비용과 판매가 입력값만 저장하고, 비교 결과는 조회할 때 계산한다.

계산식: 남는 금액 = 판매가 - 개당 비용 합 - (판매가 × 판매 수수료 %).
세금과 입력하지 않은 비용은 포함하지 않는다. 모르는 값은 0으로 채우지 않고 계산하지 않는다.
"""
from decimal import ROUND_HALF_UP, Decimal

from bson import Int64, ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.services import items
from app.services.store import conflict, save_revision, touch_project, upsert_revision

# 상품 1개 기준 비용(원)
COST_KEYS = ["material", "labor", "packaging", "shipping", "other"]
FEE_KEY = "fee_rate"
EMPTY = {**dict.fromkeys(COST_KEYS), FEE_KEY: None, "price": None}
CANDIDATE_STEP = 5000


def incomplete() -> AppError:
    return AppError(422, "COSTS_INCOMPLETE", "비용과 판매가를 모두 입력해 주세요. 비용이 없으면 0을 입력하세요.")


def calculate(cost: dict, price: int) -> dict:
    total = sum(int(cost[key]) for key in COST_KEYS)
    fee = int((Decimal(price) * Decimal(str(cost[FEE_KEY])) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return {"price": price, "cost": total, "fee": fee, "net": price - total - fee}


def _costs_ready(cost: dict | None) -> bool:
    return cost is not None and all(cost.get(key) is not None for key in (*COST_KEYS, FEE_KEY))


def compare(cost: dict, base: dict | None, confirmed_price: int | None) -> dict:
    """변경 전 기준(before), 기존 판매가 유지(keep), 판매가 변경(change)을 나란히 계산한다."""
    ready = _costs_ready(cost)
    before = calculate(base, base["price"]) if base else None
    # 유지할 가격은 저장해 둔 기준의 판매가이고, 기준이 없으면 상품기획에 확정한 가격이다.
    keep_price = base["price"] if base else confirmed_price
    keep = calculate(cost, keep_price) if ready and keep_price is not None else None
    change = calculate(cost, cost["price"]) if ready and cost.get("price") is not None else None
    for result in (keep, change):
        if result is not None:
            # 변경 전보다 개당 얼마나 더 남는지
            result["delta"] = result["net"] - before["net"] if before else None
    candidates = []
    if change is not None:
        price = change["price"]
        for candidate in dict.fromkeys([max(0, price - CANDIDATE_STEP), price, price + CANDIDATE_STEP]):
            candidates.append(calculate(cost, candidate))
    return {"before": before, "keep": keep, "change": change, "candidates": candidates}


async def _find_sheet(db: AsyncDatabase, project_id: ObjectId) -> dict | None:
    return await db.cost_sheets.find_one({"project_id": project_id})


async def _confirmed_price(db: AsyncDatabase, project_id: ObjectId) -> int | None:
    return (await items.get_item(db, project_id, "plan", items.PRICE_KEY))["amount"]


async def _view(db: AsyncDatabase, project_id: ObjectId, document: dict | None) -> dict:
    document = document or {}
    cost = {**EMPTY, **(document.get("cost") or {})}
    base = document.get("base")
    confirmed = await _confirmed_price(db, project_id)
    return {
        "project_id": project_id,
        "cost": cost,
        "base": base,
        "confirmed_price": confirmed,
        **compare(cost, base, confirmed),
        "revision": document.get("revision", 0),
        "updated_at": document.get("updated_at"),
    }


async def get_economics(db: AsyncDatabase, project_id: ObjectId) -> dict:
    return await _view(db, project_id, await _find_sheet(db, project_id))


async def save_costs(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, cost: dict, revision: int) -> dict:
    """지금 입력한 비용과 비교할 판매가를 저장한다. 비교 기준과 확정 가격은 바뀌지 않는다."""
    stored = {key: Int64(value) if isinstance(value, int) else value for key, value in cost.items()}
    existing = await _find_sheet(db, project_id)
    changes = {"cost": stored} if existing else {"cost": stored, "base": None}
    ident = {"user_id": user_id, "project_id": project_id}
    document = await upsert_revision(db.cost_sheets, ident, existing, changes, revision)
    await touch_project(db, project_id)
    return await _view(db, project_id, document)


async def save_baseline(db: AsyncDatabase, project_id: ObjectId, revision: int) -> dict:
    """현재 입력을 비교 기준으로 저장한다. 기준은 이 요청으로만 바뀐다."""
    existing = await _find_sheet(db, project_id)
    cost = (existing or {}).get("cost")
    if not _costs_ready(cost) or cost.get("price") is None:
        raise incomplete()
    document = await save_revision(db.cost_sheets, existing, {"base": cost}, revision)
    await touch_project(db, project_id, "경제성 비교 기준 저장")
    return await _view(db, project_id, document)


async def confirm_price(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, mode: str, revision: int) -> dict:
    """고른 판매가를 상품기획의 가격결정에 반영한다. keep은 기존 판매가 유지, change는 입력한 판매가로 변경."""
    existing = await _find_sheet(db, project_id)
    current = await _view(db, project_id, existing)
    result = current[mode]
    if existing is None or result is None:
        raise incomplete()
    if revision != existing.get("revision", 1):
        raise conflict()
    price = result["price"]
    # 기획 항목을 먼저 저장한다. 여기서 실패하면 경제성 입력은 바뀌지 않은 채로 남는다.
    item = await items.get_item(db, project_id, "plan", items.PRICE_KEY)
    await items.save_item(
        db, user_id, project_id, "plan", items.PRICE_KEY, item["revision"],
        content=f"{price:,}원 · 경제성 검토 후 직접 결정", amount=price,
        log=f"판매가 {price:,}원 {'유지' if mode == 'keep' else '변경'}",
    )
    document = await save_revision(
        db.cost_sheets, existing, {"cost": {**existing["cost"], "price": Int64(price)}}, revision
    )
    return await _view(db, project_id, document)
