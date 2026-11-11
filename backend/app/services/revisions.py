"""버전(revision) 조건부 저장. 읽은 뒤 다른 곳에서 바뀐 문서를 덮어쓰지 않게 한다."""
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError

from app.errors import AppError


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def conflict() -> AppError:
    return AppError(409, "REVISION_CONFLICT", "다른 곳에서 먼저 수정되었습니다. 최신 내용을 확인해 주세요.")


async def save_revision(collection: AsyncCollection, document: dict, changes: dict, revision: int) -> dict:
    """요청의 revision이 문서와 같을 때만 저장하고 revision을 1 올린다."""
    current = document.get("revision", 1)
    if revision != current:
        raise conflict()
    # revision 필드가 없는 예전 문서는 1로 본다.
    match = {"revision": current} if "revision" in document else {"revision": {"$exists": False}}
    updated = await collection.find_one_and_update(
        {"_id": document["_id"], **match},
        {"$set": {**changes, "revision": current + 1, "updated_at": utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise conflict()
    return updated


async def upsert_revision(
    collection: AsyncCollection, ident: dict, existing: dict | None, changes: dict, revision: int
) -> dict:
    """프로젝트당 하나뿐인 문서를 저장한다. 아직 없으면 revision 0으로 보내야 만들어진다."""
    if existing is not None:
        return await save_revision(collection, existing, changes, revision)
    if revision != 0:
        raise conflict()
    now = utcnow()
    document = {**ident, **changes, "revision": 1, "created_at": now, "updated_at": now}
    try:
        await collection.insert_one(document)
    except DuplicateKeyError:
        raise conflict() from None
    return document
