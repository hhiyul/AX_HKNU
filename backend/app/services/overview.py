"""프로젝트 개요(작업실 홈)와 AI에 넘길 맥락(Projection)."""
import hashlib

from pymongo.asynchronous.database import AsyncDatabase

from app.services import economics, items, projects

SECTIONS = ["project_basic", "linked_brand", *items.PLAN_LABELS]


async def _brand(db: AsyncDatabase, project: dict) -> dict | None:
    if not project.get("brand_id"):
        return None
    return await db.brands.find_one({"_id": project["brand_id"]})


async def get_overview(db: AsyncDatabase, project: dict) -> dict:
    """작업실 홈에 필요한 것을 한 번에 모은다. 메모·최근 작업·다음 할 일은 project 안에 있다."""
    project_id = project["_id"]
    ident = {"project_id": project_id}
    brand = await _brand(db, project)
    sheet = await economics.get_economics(db, project_id)
    return {
        "project": projects.view(project),
        "brand": {"id": brand["_id"], "name": brand.get("name")} if brand else None,
        "plan": [
            {"key": item["key"], "title": item["title"], "state": item["state"]}
            for item in await items.list_items(db, project_id, "plan")
        ],
        "counts": {
            "records": await db.items.count_documents({**ident, "kind": "record"}),
            "documents": await db.items.count_documents({**ident, "kind": "document"}),
            "messages": project.get("message_count", 0),
            "assets": await db.assets.count_documents(ident),
        },
        "economics": {
            "confirmed_price": sheet["confirmed_price"],
            "has_baseline": sheet["base"] is not None,
        },
        "active_jobs": await db.ai_jobs.count_documents({**ident, "status": {"$in": ["queued", "running"]}}),
    }


async def get_projection(db: AsyncDatabase, project: dict, wanted: list[str]) -> dict:
    """AI가 읽을 프로젝트 맥락. 요청한 섹션만 담고, 섹션 버전을 묶어 bundle_revision을 만든다."""
    sections: dict[str, dict] = {}
    if "project_basic" in wanted:
        sections["project_basic"] = {
            "revision": project.get("revision", 1),
            "name": project.get("name"),
            "description": project.get("description"),
            "stage": project.get("stage", "planning"),
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
                "content": item["content"],
                "amount": item["amount"],
            }
    stamp = "|".join(f"{name}:{sections[name]['revision']}" for name in SECTIONS if name in sections)
    return {
        "project_id": project["_id"],
        "bundle_revision": hashlib.sha256(stamp.encode()).hexdigest()[:16],
        "sections": sections,
    }
