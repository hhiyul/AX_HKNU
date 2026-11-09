from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError


def ensure_completable(document: dict, changes: dict) -> None:
    merged = {**document, **changes}
    if merged.get("status") == "completed" and not merged.get("name"):
        raise AppError(422, "NAME_REQUIRED", "완료하려면 이름을 입력해 주세요.")


async def save_changes(collection: AsyncCollection, document: dict, changes: dict) -> dict:
    if not changes:
        return document
    return await collection.find_one_and_update(
        {"_id": document["_id"]},
        {"$set": {**changes, "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )


async def remove_jobs(db: AsyncDatabase, query: dict, in_use: AppError) -> None:
    """삭제할 브랜드·상품의 작업 기록을 함께 지운다. 진행 중인 작업이 있으면 막는다."""
    if await db.ai_jobs.find_one({**query, "status": {"$in": ["queued", "running"]}}):
        raise in_use
    await db.ai_jobs.delete_many(query)
