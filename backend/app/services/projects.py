from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.schemas.projects import ProjectCreate, ProjectUpdate
from app.services.drafts import remove_jobs
from app.services.ownership import get_owned_brand, get_owned_project
from app.services.revisions import conflict, save_revision, utcnow
from app.storage import delete_file

# 프로젝트에 딸린 자료. 프로젝트를 지우면 함께 지운다.
CHILD_COLLECTIONS = [
    "plan_items",
    "plan_versions",
    "records",
    "cost_sheets",
    "scenarios",
    "documents",
    "document_versions",
    "proposals",
    "chats",
    "messages",
]


async def create_project(db: AsyncDatabase, user_id: ObjectId, body: ProjectCreate) -> dict:
    brand = await get_owned_brand(db, user_id, body.brand_id) if body.brand_id else None
    now = utcnow()
    project = {
        **body.model_dump(),
        "user_id": user_id,
        "brand_id": brand["_id"] if brand else None,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
    }
    await db.projects.insert_one(project)
    return project


async def list_projects(db: AsyncDatabase, user_id: ObjectId, brand_id: str | None, page) -> list[dict]:
    query = {"user_id": user_id}
    if brand_id is not None:
        query["brand_id"] = (await get_owned_brand(db, user_id, brand_id))["_id"]
    # 최근에 작업한 프로젝트가 먼저 나온다.
    cursor = db.projects.find(query).sort("updated_at", -1)
    return await cursor.skip(page.skip).limit(page.limit).to_list()


async def update_project(db: AsyncDatabase, user_id: ObjectId, project_id: str, body: ProjectUpdate) -> dict:
    project = await get_owned_project(db, user_id, project_id)
    changes = body.model_dump(exclude_unset=True)
    revision = changes.pop("revision")
    if changes.get("brand_id") is not None:
        changes["brand_id"] = (await get_owned_brand(db, user_id, changes["brand_id"]))["_id"]
    if not changes:
        if revision != project.get("revision", 1):
            raise conflict()
        return project
    return await save_revision(db.projects, project, changes, revision)


async def touch_project(db: AsyncDatabase, project_id: ObjectId) -> None:
    """하위 자료가 바뀌면 프로젝트의 최근 작업 시각만 갱신한다. revision은 그대로 둔다."""
    await db.projects.update_one({"_id": project_id}, {"$set": {"updated_at": utcnow()}})


async def delete_project(db: AsyncDatabase, user_id: ObjectId, project_id: str) -> None:
    """프로젝트와 딸린 자료를 모두 지운다. 휴지통이나 복구는 없다."""
    project = await get_owned_project(db, user_id, project_id)
    ident = {"project_id": project["_id"]}
    await remove_jobs(db, ident, AppError(409, "PROJECT_IN_USE", "프로젝트에 진행 중인 작업이 있습니다."))
    # 브랜드에도 속한 파일은 남기고 연결만 푼다. 프로젝트에만 속한 파일은 지운다.
    await db.assets.update_many({**ident, "brand_id": {"$ne": None}}, {"$set": {"project_id": None}})
    async for asset in db.assets.find(ident):
        await db.assets.delete_one({"_id": asset["_id"]})
        await delete_file(asset["_id"])
    for name in CHILD_COLLECTIONS:
        await db[name].delete_many(ident)
    # 다른 프로젝트의 채팅이 이 프로젝트를 참고 자료로 걸어 둔 경우
    await db.chats.update_many({"chip_refs": project["_id"]}, {"$pull": {"chip_refs": project["_id"]}})
    await db.projects.delete_one({"_id": project["_id"]})
