"""중복 요청 방지와 요청 결과 조회.

저장 요청에 Idempotency-Key 헤더를 붙이면 같은 키의 요청은 한 번만 처리된다.
응답을 못 받았을 때는 다시 보내지 말고 GET /requests/{key}로 반영 여부를 확인한다.
"""
from collections.abc import Awaitable, Callable
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.errors import AppError
from app.services.revisions import utcnow


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
