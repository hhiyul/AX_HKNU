"""프로젝트 개요와 AI에 넘길 맥락(Projection)."""
import hashlib

from pymongo.asynchronous.database import AsyncDatabase

from app.services import economics, items

SECTIONS = ["project_basic", "linked_brand", *items.PLAN_LABELS]


async def _brand(db: AsyncDatabase, project: dict) -> dict | None:
    if not project.get("brand_id"):
        return None
    return await db.brands.find_one({"_id": project["brand_id"]})


async def get_overview(db: AsyncDatabase, project: dict) -> dict:
    project_id = project["_id"]
    ident = {"project_id": project_id}
    brand = await _brand(db, project)
    documents = await items.list_items(db, project_id, "document")
    sheet = await economics.get_sheet(db, project_id)
    adopted = await db.scenarios.find_one({**ident, "adopted_at": {"$ne": None}}, sort=[("adopted_at", -1)])
    return {
        "project": project,
        "brand": {"id": brand["_id"], "name": brand.get("name"), "status": brand["status"]} if brand else None,
        "plan": [
            {"key": item["key"], "title": item["title"], "state": item["state"], "revision": item["revision"]}
            for item in await items.list_items(db, project_id, "plan")
        ],
        "proposals": {
            "pending": await db.proposals.count_documents({**ident, "state": "PENDING"}),
            "held": await db.proposals.count_documents({**ident, "state": "HELD"}),
        },
        "documents": {
            "count": len(documents),
            "stale": [
                {"key": doc["key"], "title": doc["title"], "stale_items": doc["stale_items"]}
                for doc in documents
                if doc["stale_items"]
            ],
        },
        "records": await db.items.count_documents({**ident, "kind": "record"}),
        "economics": {
            "cost_items": len(sheet["items"]),
            "unknown_costs": sum(1 for item in sheet["items"] if item["amount"] is None),
            "scenarios": await db.scenarios.count_documents(ident),
            "adopted_price": adopted["price"] if adopted else None,
        },
        "active_jobs": await db.ai_jobs.count_documents({**ident, "status": {"$in": ["queued", "running"]}}),
    }


def _part(part: dict | None) -> dict | None:
    if part is None:
        return None
    return {"content": part["content"], "amount": part["amount"]}


async def get_projection(db: AsyncDatabase, project: dict, wanted: list[str]) -> dict:
    """AI가 읽을 프로젝트 맥락. 요청한 섹션만 담고, 섹션 버전을 묶어 bundle_revision을 만든다."""
    sections: dict[str, dict] = {}
    if "project_basic" in wanted:
        sections["project_basic"] = {
            "revision": project.get("revision", 1),
            "name": project.get("name"),
            "description": project.get("description"),
        }
    if "linked_brand" in wanted:
        brand = await _brand(db, project)
        sections["linked_brand"] = {
            # 브랜드에는 revision이 없어 수정 시각으로 대신한다.
            "revision": brand["updated_at"].isoformat() if brand else None,
            "brand": {
                key: brand.get(key) for key in ("name", "description", "target_customer", "keywords", "colors")
            }
            if brand
            else None,
        }
    for item in await items.list_items(db, project["_id"], "plan"):
        if item["key"] in wanted:
            sections[item["key"]] = {
                "revision": item["revision"],
                "label": item["title"],
                "state": item["state"],
                "draft": _part(item["draft"]),
                "decided": _part(item["decided"]),
            }
    stamp = "|".join(f"{name}:{sections[name]['revision']}" for name in SECTIONS if name in sections)
    return {
        "project_id": project["_id"],
        "bundle_revision": hashlib.sha256(stamp.encode()).hexdigest()[:16],
        "sections": sections,
    }
