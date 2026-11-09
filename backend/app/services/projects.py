from datetime import datetime, timezone

from bson import Int64, ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.schemas.projects import ProjectCreate, ProjectUpdate
from app.services.drafts import ensure_completable, remove_jobs, save_changes
from app.services.ownership import get_owned_brand, get_owned_project


def _with_int64_price(values: dict) -> dict:
    if values.get("expected_price") is not None:
        values["expected_price"] = Int64(values["expected_price"])
    return values


async def create_project(db: AsyncDatabase, user_id: ObjectId, body: ProjectCreate) -> dict:
    brand = await get_owned_brand(db, user_id, body.brand_id)
    now = datetime.now(timezone.utc)
    project = {
        **_with_int64_price(body.model_dump()),
        "brand_id": brand["_id"],
        "status": "draft",
        "created_at": now,
        "updated_at": now,
    }
    await db.projects.insert_one(project)
    return project


async def list_projects(db: AsyncDatabase, user_id: ObjectId, brand_id: str | None, page) -> list[dict]:
    if brand_id is not None:
        brand_ids = [(await get_owned_brand(db, user_id, brand_id))["_id"]]
    else:
        brand_ids = await db.brands.distinct("_id", {"user_id": user_id})
    cursor = db.projects.find({"brand_id": {"$in": brand_ids}}).sort("created_at", -1)
    return await cursor.skip(page.skip).limit(page.limit).to_list()


async def update_project(db: AsyncDatabase, user_id: ObjectId, project_id: str, body: ProjectUpdate) -> dict:
    project = await get_owned_project(db, user_id, project_id)
    changes = _with_int64_price(body.model_dump(exclude_unset=True))
    ensure_completable(project, changes)
    return await save_changes(db.projects, project, changes)


async def delete_project(db: AsyncDatabase, user_id: ObjectId, project_id: str) -> None:
    project = await get_owned_project(db, user_id, project_id)
    if await db.assets.find_one({"project_id": project["_id"]}):
        raise AppError(409, "PROJECT_IN_USE", "상품에 파일이 남아 있습니다.")
    in_use = AppError(409, "PROJECT_IN_USE", "상품에 진행 중인 작업이 있습니다.")
    await remove_jobs(db, {"project_id": project["_id"]}, in_use)
    await db.projects.delete_one({"_id": project["_id"]})
