import re

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.schemas.projects import ProjectCreate, ProjectUpdate
from app.services import items
from app.services.store import (
    conflict,
    get_owned_brand,
    get_owned_project,
    not_found,
    owned_ids,
    recent_push,
    remove_jobs,
    save_revision,
    utcnow,
)
from app.storage import delete_file

# 프로젝트에 딸린 자료. 프로젝트를 완전히 지우면 함께 지운다.
CHILD_COLLECTIONS = ["items", "cost_sheets", "messages"]
STAGE_LABELS = {"planning": "기획", "prototype": "시제품", "ready": "판매 준비", "selling": "판매 중"}
NO_BRAND = "none"


def status_of(project: dict) -> str:
    if project.get("deleted_at"):
        return "trash"
    return "archived" if project.get("archived_at") else "active"


def view(project: dict) -> dict:
    return {**project, "status": status_of(project)}


async def create_project(db: AsyncDatabase, user_id: ObjectId, body: ProjectCreate) -> dict:
    brand = await get_owned_brand(db, user_id, body.brand_id) if body.brand_id else None
    for answer in body.plan or []:
        items.check_plan_answer(answer.key, answer.content, answer.amount)
    now = utcnow()
    project = {
        **body.model_dump(exclude={"plan"}),
        "user_id": user_id,
        "brand_id": brand["_id"] if brand else None,
        "archived_at": None,
        "deleted_at": None,
        "note": None,
        "next_action": None,
        "plan_filled": 0,
        "recent": recent_push("프로젝트 생성")["recent"]["$each"],
        "search_enabled": False,
        "file_refs": [],
        "chip_refs": [],
        "message_count": 0,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
    }
    await db.projects.insert_one(project)
    # 기획 6개 항목은 늘 함께 만든다. 마법사에서 답하지 않았거나 마법사를 거치지 않은 항목은 "건너뜀"이다.
    answers = {answer.key: answer for answer in body.plan or []}
    project["plan_filled"] = await items.start_plan(db, user_id, project["_id"], answers)
    return view(project)


async def list_projects(
    db: AsyncDatabase, user_id: ObjectId, brand_id: str | None, status: str, q: str | None, page
) -> list[dict]:
    query: dict = {"user_id": user_id}
    if brand_id == NO_BRAND:
        query["brand_id"] = None
    elif brand_id is not None:
        query["brand_id"] = (await get_owned_brand(db, user_id, brand_id))["_id"]
    if status == "trash":
        query["deleted_at"] = {"$ne": None}
    else:
        query["deleted_at"] = None
        query["archived_at"] = {"$ne": None} if status == "archived" else None
    if q and q.strip():
        query["name"] = {"$regex": re.escape(q.strip()), "$options": "i"}
    # 최근에 작업한 프로젝트가 먼저 나온다.
    cursor = db.projects.find(query).sort("updated_at", -1)
    return [view(project) for project in await cursor.skip(page.skip).limit(page.limit).to_list()]


def _status_changes(project: dict, status: str) -> tuple[dict, str | None]:
    current = status_of(project)
    if status == current:
        return {}, None
    now = utcnow()
    if status == "trash":
        return {"deleted_at": now}, "휴지통으로 이동"
    if status == "archived":
        return {"archived_at": now, "deleted_at": None}, "프로젝트 보관"
    log = "휴지통에서 복원" if current == "trash" else "프로젝트 보관 해제"
    return {"archived_at": None, "deleted_at": None}, log


async def update_project(db: AsyncDatabase, user_id: ObjectId, project_id: str, body: ProjectUpdate) -> dict:
    project = await get_owned_project(db, user_id, project_id)
    changes = body.model_dump(exclude_unset=True)
    revision = changes.pop("revision")
    log = None
    if changes.get("brand_id") is not None:
        changes["brand_id"] = (await get_owned_brand(db, user_id, changes["brand_id"]))["_id"]
    if "file_refs" in changes:
        changes["file_refs"] = await owned_ids(db, "assets", user_id, changes["file_refs"])
    if "chip_refs" in changes:
        changes["chip_refs"] = await owned_ids(db, "projects", user_id, changes["chip_refs"])
        # 자기 자신은 참고 프로젝트로 걸 수 없다.
        if project["_id"] in changes["chip_refs"]:
            raise not_found()
    if {"name", "brand_id", "description"} & changes.keys():
        log = "프로젝트 설정 변경"
    if "note" in changes:
        log = "나만의 메모 수정"
    old_stage = project.get("stage", "planning")
    if changes.get("stage", old_stage) != old_stage:
        log = f"진행 단계 변경 · {STAGE_LABELS[old_stage]} → {STAGE_LABELS[changes['stage']]}"
    if "status" in changes:
        status_changes, status_log = _status_changes(project, changes.pop("status"))
        changes.update(status_changes)
        log = status_log or log
    if not changes:
        if revision != project.get("revision", 1):
            raise conflict()
        return view(project)
    return view(await save_revision(db.projects, project, changes, revision, log))


async def delete_project(db: AsyncDatabase, user_id: ObjectId, project_id: str, permanent: bool) -> None:
    """휴지통으로 옮긴다. permanent면 휴지통에 있는 프로젝트를 딸린 자료까지 완전히 지운다."""
    project = await get_owned_project(db, user_id, project_id)
    if not permanent:
        if not project.get("deleted_at"):
            now = utcnow()
            await db.projects.update_one(
                {"_id": project["_id"]},
                {"$set": {"deleted_at": now, "updated_at": now}, "$inc": {"revision": 1}},
            )
        return
    if not project.get("deleted_at"):
        raise AppError(409, "NOT_IN_TRASH", "휴지통에 있는 프로젝트만 완전히 삭제할 수 있습니다.")
    ident = {"project_id": project["_id"]}
    await remove_jobs(db, ident, AppError(409, "PROJECT_IN_USE", "프로젝트에 진행 중인 작업이 있습니다."))
    # 브랜드에도 속한 파일은 남기고 연결만 푼다. 프로젝트에만 속한 파일은 지운다.
    await db.assets.update_many({**ident, "brand_id": {"$ne": None}}, {"$set": {"project_id": None}})
    async for asset in db.assets.find(ident):
        await db.assets.delete_one({"_id": asset["_id"]})
        await delete_file(asset["_id"])
        # 다른 프로젝트가 이 파일을 참고 파일로 걸어 둔 경우
        await db.projects.update_many({"file_refs": asset["_id"]}, {"$pull": {"file_refs": asset["_id"]}})
    for name in CHILD_COLLECTIONS:
        await db[name].delete_many(ident)
    # 다른 프로젝트가 이 프로젝트를 참고 자료로 걸어 둔 경우
    await db.projects.update_many({"chip_refs": project["_id"]}, {"$pull": {"chip_refs": project["_id"]}})
    await db.projects.delete_one({"_id": project["_id"]})
