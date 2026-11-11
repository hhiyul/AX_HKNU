"""경제성. 비용 입력과 가격 시나리오.

계산식은 임시 기준이다: 이익 = 판매가 - 원 단위 비용 합 - (판매가 × 비율 비용 합).
세금·배송 배부·반올림 같은 실제 기준은 팀에서 정한 뒤 compute()만 고치면 된다.
"""
from decimal import ROUND_HALF_UP, Decimal

from bson import Int64, ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.services import plan
from app.services.ownership import get_owned
from app.services.projects import touch_project
from app.services.revisions import upsert_revision, utcnow


def sheet_view(project_id: ObjectId, document: dict | None) -> dict:
    document = document or {}
    return {
        "project_id": project_id,
        "items": document.get("items", []),
        "revision": document.get("revision", 0),
        "updated_at": document.get("updated_at"),
    }


async def _find_sheet(db: AsyncDatabase, project_id: ObjectId) -> dict | None:
    return await db.cost_sheets.find_one({"project_id": project_id})


async def get_sheet(db: AsyncDatabase, project_id: ObjectId) -> dict:
    return sheet_view(project_id, await _find_sheet(db, project_id))


async def save_sheet(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, items: list, revision: int) -> dict:
    stored = []
    for item in items:
        values = item.model_dump()
        if values["amount"] is not None and values["unit"] == "WON":
            values["amount"] = Int64(int(values["amount"]))
        stored.append(values)
    ident = {"user_id": user_id, "project_id": project_id}
    document = await upsert_revision(db.cost_sheets, ident, await _find_sheet(db, project_id), {"items": stored}, revision)
    await touch_project(db, project_id)
    return sheet_view(project_id, document)


def compute(items: list[dict], price: int) -> dict:
    """모르는 값과 승인하지 않은 가정은 0으로 채우지 않고 계산에서 빼며, 뺐다는 사실을 남긴다."""
    unit_cost, rate, excluded, uses_assumptions = 0, Decimal(0), [], False
    for item in items:
        if item["amount"] is None:
            excluded.append({"label": item["label"], "reason": "UNKNOWN"})
            continue
        if item["basis"] == "ASSUMPTION":
            if not item["approved"]:
                excluded.append({"label": item["label"], "reason": "UNAPPROVED_ASSUMPTION"})
                continue
            uses_assumptions = True
        if item["unit"] == "WON":
            unit_cost += int(item["amount"])
        else:
            rate += Decimal(str(item["amount"]))
    fee = int((Decimal(price) * rate / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    profit = price - unit_cost - fee
    return {
        "unit_cost": unit_cost,
        "fee": fee,
        "profit": profit,
        "margin_rate": round(profit / price * 100, 1) if price else None,
        "complete": bool(items) and not excluded,
        "uses_assumptions": uses_assumptions,
        "excluded": excluded,
    }


async def create_scenario(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, price: int, label: str) -> dict:
    sheet = await get_sheet(db, project_id)
    scenario = {
        "user_id": user_id,
        "project_id": project_id,
        "label": label,
        "price": price,
        **compute(sheet["items"], price),
        "sheet_revision": sheet["revision"],
        "adopted_at": None,
        "created_at": utcnow(),
    }
    await db.scenarios.insert_one(scenario)
    await touch_project(db, project_id)
    return scenario


async def list_scenarios(db: AsyncDatabase, project_id: ObjectId, page) -> list[dict]:
    cursor = db.scenarios.find({"project_id": project_id}).sort("created_at", -1)
    return await cursor.skip(page.skip).limit(page.limit).to_list()


async def delete_scenario(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, scenario_id: str) -> None:
    scenario = await get_owned(db, "scenarios", user_id, scenario_id, project_id=project_id)
    await db.scenarios.delete_one({"_id": scenario["_id"]})


async def adopt_scenario(
    db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, scenario_id: str, revision: int
) -> dict:
    """고른 가격을 가격·원가 항목의 초안으로 넘긴다. 확정은 상품기획에서 따로 한다."""
    scenario = await get_owned(db, "scenarios", user_id, scenario_id, project_id=project_id)
    if scenario["price"] > 2**63 - 1:
        raise AppError(422, "VALIDATION_ERROR", "입력값을 확인해 주세요: price")
    content = f"판매가 {scenario['price']:,}원"
    if scenario["label"]:
        content += f" ({scenario['label']})"
    item = await plan.save_item(
        db, user_id, project_id, "ck_05", "DRAFT", content, scenario["price"], revision, "ECONOMICS", scenario["_id"]
    )
    scenario["adopted_at"] = utcnow()
    await db.scenarios.update_one({"_id": scenario["_id"]}, {"$set": {"adopted_at": scenario["adopted_at"]}})
    return {"scenario": scenario, "item": item}
