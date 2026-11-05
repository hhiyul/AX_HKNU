from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection

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
