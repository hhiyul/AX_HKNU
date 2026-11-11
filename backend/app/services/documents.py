"""기획 산출물. 문서는 기획 항목과 따로 저장되고, 기획이 바뀌어도 저절로 덮어쓰지 않는다."""
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.services import plan
from app.services.ownership import get_owned
from app.services.projects import touch_project
from app.services.revisions import save_revision, utcnow


def view(document: dict, versions: dict[str, int]) -> dict:
    snapshot = document.get("plan_snapshot", {})
    return {
        **document,
        "uses_unconfirmed": any(basis in ("DRAFT", "PROPOSAL") for basis in document["basis"]),
        # 문서를 만들거나 최신화한 뒤에 확정본이 바뀐 항목
        "stale_items": [key for key, version in versions.items() if version != snapshot.get(key, 0)],
    }


async def _owned(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, document_id: str) -> dict:
    return await get_owned(db, "documents", user_id, document_id, project_id=project_id)


async def create_document(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, body) -> dict:
    versions = await plan.decided_versions(db, project_id)
    now = utcnow()
    document = {
        **body.model_dump(),
        "user_id": user_id,
        "project_id": project_id,
        "state": "DRAFT",
        "plan_snapshot": versions,
        "version": 0,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
    }
    await db.documents.insert_one(document)
    await touch_project(db, project_id)
    return view(document, versions)


async def list_documents(db: AsyncDatabase, project_id: ObjectId, page) -> list[dict]:
    versions = await plan.decided_versions(db, project_id)
    cursor = db.documents.find({"project_id": project_id}).sort("updated_at", -1)
    return [view(doc, versions) for doc in await cursor.skip(page.skip).limit(page.limit).to_list()]


async def get_document(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, document_id: str) -> dict:
    document = await _owned(db, user_id, project_id, document_id)
    return view(document, await plan.decided_versions(db, project_id))


async def _save(db: AsyncDatabase, document: dict, changes: dict, revision: int) -> dict:
    updated = await save_revision(db.documents, document, changes, revision)
    await touch_project(db, document["project_id"])
    return view(updated, await plan.decided_versions(db, document["project_id"]))


async def update_document(
    db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, document_id: str, changes: dict, revision: int
) -> dict:
    document = await _owned(db, user_id, project_id, document_id)
    # 최종본을 고치면 다시 작성 중이 된다. 이전 최종본은 이력에 남아 있다.
    return await _save(db, document, {**changes, "state": "DRAFT"}, revision)


async def finalize_document(
    db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, document_id: str, revision: int
) -> dict:
    document = await _owned(db, user_id, project_id, document_id)
    version = document["version"] + 1
    result = await _save(db, document, {"state": "FINAL", "version": version}, revision)
    await db.document_versions.insert_one(
        {
            "user_id": user_id,
            "project_id": project_id,
            "document_id": document["_id"],
            "version": version,
            "title": document["title"],
            "content": document["content"],
            "finalized_at": utcnow(),
        }
    )
    return result


async def sync_document(
    db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, document_id: str, revision: int
) -> dict:
    """사용자가 바뀐 기획을 확인했다고 표시한다. 문서 내용은 건드리지 않는다."""
    document = await _owned(db, user_id, project_id, document_id)
    versions = await plan.decided_versions(db, project_id)
    return await _save(db, document, {"plan_snapshot": versions}, revision)


async def list_versions(
    db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, document_id: str, page
) -> list[dict]:
    document = await _owned(db, user_id, project_id, document_id)
    cursor = db.document_versions.find({"document_id": document["_id"]}).sort("version", -1)
    return await cursor.skip(page.skip).limit(page.limit).to_list()


async def delete_document(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, document_id: str) -> None:
    document = await _owned(db, user_id, project_id, document_id)
    await db.document_versions.delete_many({"document_id": document["_id"]})
    await db.documents.delete_one({"_id": document["_id"]})
