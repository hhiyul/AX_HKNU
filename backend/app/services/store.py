"""저장 공통 도우미: 내 자료 확인, 버전(revision) 조건부 저장, 중복 요청 방지."""
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.errors import AppError


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------- 내 자료 확인 ----------


def not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "자료를 찾을 수 없습니다.")


def parse_object_id(value: str | ObjectId) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise not_found() from None


async def get_owned(db: AsyncDatabase, collection: str, user_id: ObjectId, doc_id: str | ObjectId, **extra) -> dict:
    """내 자료만 꺼낸다. 남의 자료와 잘못된 ID는 모두 404."""
    document = await db[collection].find_one({"_id": parse_object_id(doc_id), "user_id": user_id, **extra})
    if document is None:
        raise not_found()
    return document


async def get_owned_brand(db: AsyncDatabase, user_id: ObjectId, brand_id: str | ObjectId) -> dict:
    return await get_owned(db, "brands", user_id, brand_id)


async def get_owned_project(db: AsyncDatabase, user_id: ObjectId, project_id: str | ObjectId) -> dict:
    return await get_owned(db, "projects", user_id, project_id)


async def get_owned_asset(db: AsyncDatabase, user_id: ObjectId, asset_id: str | ObjectId) -> dict:
    return await get_owned(db, "assets", user_id, asset_id)


async def owned_ids(db: AsyncDatabase, collection: str, user_id: ObjectId, ids: list[str]) -> list[ObjectId]:
    """id 목록이 모두 내 자료인지 확인한다. 하나라도 없거나 남의 것이면 404."""
    wanted = list(dict.fromkeys(parse_object_id(value) for value in ids))
    if wanted:
        count = await db[collection].count_documents({"_id": {"$in": wanted}, "user_id": user_id})
        if count != len(wanted):
            raise not_found()
    return wanted


async def remove_jobs(db: AsyncDatabase, query: dict, in_use: AppError) -> None:
    """삭제할 브랜드·프로젝트의 작업 기록을 함께 지운다. 진행 중인 작업이 있으면 막는다."""
    if await db.ai_jobs.find_one({**query, "status": {"$in": ["queued", "running"]}}):
        raise in_use
    await db.ai_jobs.delete_many(query)


# ---------- 프로젝트의 최근 작업 ----------

RECENT_LIMIT = 20


def recent_push(text: str) -> dict:
    """최근 작업 목록 맨 앞에 한 줄을 넣고 20건만 남기는 $push 값."""
    return {"recent": {"$each": [{"text": text, "at": utcnow()}], "$position": 0, "$slice": RECENT_LIMIT}}


async def touch_project(db: AsyncDatabase, project_id: ObjectId, log: str | None = None, **fields) -> None:
    """하위 자료가 바뀌면 프로젝트의 최근 작업 시각을 갱신한다. revision은 그대로 둔다."""
    update: dict = {"$set": {**fields, "updated_at": utcnow()}}
    if log:
        update["$push"] = recent_push(log)
    await db.projects.update_one({"_id": project_id}, update)


# ---------- 버전 조건부 저장 ----------
# 읽은 뒤 다른 곳에서 바뀐 문서를 덮어쓰지 않게 한다.


def conflict() -> AppError:
    return AppError(409, "REVISION_CONFLICT", "다른 곳에서 먼저 수정되었습니다. 최신 내용을 확인해 주세요.")


async def save_revision(
    collection: AsyncCollection, document: dict, changes: dict, revision: int, log: str | None = None
) -> dict:
    """요청의 revision이 문서와 같을 때만 저장하고 revision을 1 올린다. log는 프로젝트의 최근 작업에 남길 글이다."""
    current = document.get("revision", 1)
    if revision != current:
        raise conflict()
    # revision 필드가 없는 예전 문서는 1로 본다.
    match = {"revision": current} if "revision" in document else {"revision": {"$exists": False}}
    update = {"$set": {**changes, "revision": current + 1, "updated_at": utcnow()}}
    if log:
        update["$push"] = recent_push(log)
    updated = await collection.find_one_and_update(
        {"_id": document["_id"], **match}, update, return_document=ReturnDocument.AFTER
    )
    if updated is None:
        raise conflict()
    return updated


async def upsert_revision(
    collection: AsyncCollection, ident: dict, existing: dict | None, changes: dict, revision: int
) -> dict:
    """아직 없을 수도 있는 문서를 저장한다. 없으면 revision 0으로 보내야 만들어진다."""
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


# ---------- 중복 요청 방지 ----------
# 저장 요청에 Idempotency-Key 헤더를 붙이면 같은 키의 요청은 한 번만 처리된다.
# 응답을 못 받았을 때는 다시 보내지 말고 GET /requests/{key}로 반영 여부를 확인한다.


async def run_once(
    db: AsyncDatabase, user_id: ObjectId, key: str | None, make: Callable[[], Awaitable[Any]]
) -> Any:
    if key is None:
        return await make()
    ident = {"user_id": user_id, "key": key}
    try:
        await db.requests.insert_one({**ident, "state": "PROCESSING", "created_at": utcnow()})
    except DuplicateKeyError:
        record = await db.requests.find_one(ident)
        if record is None or record["state"] == "PROCESSING":
            raise AppError(409, "REQUEST_IN_PROGRESS", "같은 요청을 처리하고 있습니다. 결과를 조회해 주세요.") from None
        if record["state"] == "NOT_APPLIED":
            error = record["error"]
            raise AppError(error["status"], error["code"], error["message"]) from None
        return record["result"]

    try:
        result = await make()
    except AppError as exc:
        error = {"status": exc.status, "code": exc.code, "message": exc.message}
        await db.requests.update_one(ident, {"$set": {"state": "NOT_APPLIED", "error": error}})
        raise
    # 예상하지 못한 오류면 PROCESSING으로 남는다. 반영 여부를 알 수 없기 때문이다.
    await db.requests.update_one(ident, {"$set": {"state": "APPLIED", "result": result}})
    return result


async def get_request(db: AsyncDatabase, user_id: ObjectId, key: str) -> dict:
    record = await db.requests.find_one({"user_id": user_id, "key": key})
    if record is None:
        raise AppError(404, "NOT_FOUND", "이 키로 접수된 요청이 없습니다.")
    return record
