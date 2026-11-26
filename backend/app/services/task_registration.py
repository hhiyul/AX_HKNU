"""확인한 신규 후보만 등록한다. 응답 유실은 원 요청 조회로 해결하며 쓰기를 재전송하지 않는다."""
import hashlib

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.errors import AppError
from app.services import tasks
from app.services.store import not_found, utcnow


def identity(user_id, project_id, request_id):
    return f"{user_id}:{project_id}:{request_id}"


async def result(db, user_id, project_id, request_id):
    operation = await db.task_registrations.find_one({"_id": identity(user_id, project_id, request_id)})
    if operation is None:
        raise not_found()
    rows = []
    for row in operation["results"]:
        row = dict(row)
        if row["status"] != "failed" and ObjectId.is_valid(row["proposal_id"]):
            stored = await db.tasks.find_one({"project_id": project_id, "source_proposal_id": ObjectId(row["proposal_id"])})
            if stored:
                row.update(status="already_linked" if row["status"] == "unknown" else row["status"], task=await tasks.task_view(db, stored, existing=True))
        rows.append(row)
    return {"request_id": request_id, "results": rows}


async def register(db, user_id, project_id, body):
    ids = [t.source_proposal_id for t in body.tasks]
    if any(not ident or not ObjectId.is_valid(ident) for ident in ids) or len(set(ids)) != len(ids):
        raise AppError(422, "INVALID_PROPOSALS", "등록할 후보를 중복 없이 선택해 주세요.")
    key = identity(user_id, project_id, body.request_id)
    fingerprint = hashlib.sha256(body.model_dump_json(exclude={"request_id"}).encode()).hexdigest()
    rows = [{"proposal_id": ident, "status": "unknown"} for ident in ids]
    try:
        await db.task_registrations.insert_one({"_id": key, "user_id": user_id, "project_id": project_id,
                                                "fingerprint": fingerprint, "confirmed": body.model_dump(mode="json")["tasks"],
                                                "results": rows, "created_at": utcnow()})
    except DuplicateKeyError:
        existing = await db.task_registrations.find_one({"_id": key})
        if existing["fingerprint"] != fingerprint:
            raise AppError(409, "REQUEST_CONTENT_CHANGED", "같은 요청의 확인 내용이 달라졌습니다. 원 요청 결과를 확인해 주세요.")
        return await result(db, user_id, project_id, body.request_id)
    for index, task in enumerate(body.tasks):
        try:
            saved, created = await tasks.create_task(db, user_id, project_id, task)
            row = {"proposal_id": task.source_proposal_id, "status": "registered" if created else "already_linked", "task": saved}
        except AppError as exc:
            row = {"proposal_id": task.source_proposal_id, "status": "failed", "error": {"code": exc.code, "message": exc.message}}
        # DB/프로세스 실패는 unknown으로 남긴다. 이전 성공 결과는 덮거나 되돌리지 않는다.
        await db.task_registrations.update_one({"_id": key}, {"$set": {f"results.{index}": row}})
    return await result(db, user_id, project_id, body.request_id)
