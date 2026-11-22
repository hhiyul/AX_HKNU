"""AI 채팅 실행 기록(ai_runs)과 프로젝트의 실행 잠금(active_run), 압축한 예전 대화(chat_capsule).

같은 Atlas를 쓰는 서버가 여럿일 수 있다. 그래서 실행마다 heartbeat_at을 갱신하고,
60초 넘게 멈춘 실행만 죽은 것으로 정리한다. 살아 있는 남의 실행은 건드리지 않는다.
"""
from datetime import timedelta
from enum import Enum

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.ai.context import ContextError
from app.errors import AppError
from app.services.store import not_found, parse_object_id, utcnow

HEARTBEAT_SECONDS = 15
STALE_SECONDS = 60
ACTIVE_PROJECT = {"deleted_at": None, "archived_at": None}


class RunCheck(str, Enum):
    OK = "ok"
    STOP_REQUESTED = "stop_requested"
    PROJECT_NOT_ACTIVE = "project_not_active"
    # 이미 끝났거나 정리되어 이 실행이 더는 프로젝트의 실행이 아니다.
    LOST = "lost"


async def _lock(db: AsyncDatabase, project_id: ObjectId, run_id: ObjectId) -> bool:
    locked = await db.projects.find_one_and_update(
        {"_id": project_id, "active_run": None, **ACTIVE_PROJECT}, {"$set": {"active_run": run_id}}
    )
    return locked is not None


async def _clear_orphaned_lock(db: AsyncDatabase, project_id: ObjectId) -> None:
    """잠금이 가리키는 실행이 없거나 이미 끝났으면 잠금을 푼다(실행을 끝낸 뒤 잠금 해제가 실패한 경우)."""
    project = await db.projects.find_one({"_id": project_id}, {"active_run": 1})
    held = (project or {}).get("active_run")
    if held is None:
        return
    run = await db.ai_runs.find_one({"_id": held}, {"status": 1})
    if run is None or run["status"] != "running":
        await db.projects.update_one({"_id": project_id, "active_run": held}, {"$set": {"active_run": None}})


async def begin_run(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId) -> ObjectId:
    """프로젝트를 잠그고 running 실행을 만든다. 사용자 메시지는 잠근 뒤 attach_user_message로 붙인다."""
    run_id = ObjectId()
    if not await _lock(db, project_id, run_id):
        await reap_stale(db, project_id)
        await _clear_orphaned_lock(db, project_id)
        if not await _lock(db, project_id, run_id):
            project = await db.projects.find_one({"_id": project_id})
            if project is None:
                raise not_found()
            if project.get("deleted_at") or project.get("archived_at"):
                raise AppError(409, "PROJECT_NOT_ACTIVE", "보관함이나 휴지통에 있는 프로젝트입니다. 먼저 복원해 주세요.")
            raise AppError(409, "RUN_ACTIVE", "이미 답변을 만들고 있습니다.")
    now = utcnow()
    await db.ai_runs.insert_one(
        {
            "_id": run_id,
            "user_id": user_id,
            "project_id": project_id,
            "status": "running",
            "user_message_id": None,
            "assistant_message_id": None,
            "heartbeat_at": now,
            "stop_requested": False,
            "created_at": now,
            "updated_at": now,
        }
    )
    return run_id


async def attach_user_message(db: AsyncDatabase, run_id: ObjectId, message_id: ObjectId) -> None:
    await db.ai_runs.update_one({"_id": run_id}, {"$set": {"user_message_id": message_id}})


async def heartbeat(db: AsyncDatabase, run_id: ObjectId) -> None:
    await db.ai_runs.update_one({"_id": run_id, "status": "running"}, {"$set": {"heartbeat_at": utcnow()}})


async def check(db: AsyncDatabase, run_id: ObjectId, project_id: ObjectId) -> RunCheck:
    run = await db.ai_runs.find_one({"_id": run_id})
    project = await db.projects.find_one({"_id": project_id})
    if run is None or run["status"] != "running" or project is None or project.get("active_run") != run_id:
        return RunCheck.LOST
    if project.get("deleted_at") or project.get("archived_at"):
        return RunCheck.PROJECT_NOT_ACTIVE
    if run.get("stop_requested"):
        return RunCheck.STOP_REQUESTED
    return RunCheck.OK


async def request_stop(db: AsyncDatabase, run_id: ObjectId) -> None:
    """실행을 갖지 않은 서버가 받은 중지 요청. 실행 쪽이 check로 확인하고 멈춘다."""
    await db.ai_runs.update_one({"_id": run_id, "status": "running"}, {"$set": {"stop_requested": True}})


async def finish_run(
    db: AsyncDatabase,
    run_id: ObjectId,
    project_id: ObjectId,
    status: str,
    error_code: str | None = None,
    delivery: str | None = None,
    usage: dict | None = None,
    assistant_message_id: ObjectId | None = None,
) -> None:
    """running인 실행만 끝낸다. 잠금은 그 실행이 쥐고 있을 때만 푼다."""
    now = utcnow()
    changes = {
        "status": status,
        "error_code": error_code,
        "delivery": delivery,
        "assistant_message_id": assistant_message_id,
        "finished_at": now,
        "updated_at": now,
    }
    if usage is not None:
        changes["usage"] = usage
    await db.ai_runs.update_one({"_id": run_id, "status": "running"}, {"$set": changes})
    await db.projects.update_one({"_id": project_id, "active_run": run_id}, {"$set": {"active_run": None}})


async def reap_stale(db: AsyncDatabase, project_id: ObjectId | None = None) -> int:
    """heartbeat가 60초 넘게 멈춘 running 실행을 unknown으로 끝내고 잠금을 푼다. 정리한 개수를 돌려준다."""
    cutoff = utcnow() - timedelta(seconds=STALE_SECONDS)
    query: dict = {"status": "running", "heartbeat_at": {"$lt": cutoff}}
    if project_id is not None:
        query["project_id"] = project_id
    reaped = 0
    async for run in db.ai_runs.find(query, {"project_id": 1}):
        now = utcnow()
        result = await db.ai_runs.update_one(
            {"_id": run["_id"], **query},
            {
                "$set": {
                    "status": "unknown",
                    "error_code": "PROCESS_RESTART",
                    "delivery": "unknown",
                    "finished_at": now,
                    "updated_at": now,
                }
            },
        )
        if result.modified_count:
            reaped += 1
            await db.projects.update_one(
                {"_id": run["project_id"], "active_run": run["_id"]}, {"$set": {"active_run": None}}
            )
    return reaped


async def load_capsule(db: AsyncDatabase, project_id: ObjectId) -> dict | None:
    project = await db.projects.find_one({"_id": project_id}, {"chat_capsule": 1})
    return (project or {}).get("chat_capsule")


async def save_capsule(
    db: AsyncDatabase,
    project_id: ObjectId,
    run_id: ObjectId,
    expected_revision: int,
    text: str,
    covered_ids: list[str],
    source_ids: list[str] | None = None,
) -> int:
    """요약 번호와 실행 잠금이 맞을 때만 압축한 예전 대화를 바꾼다. 프로젝트 revision은 건드리지 않는다."""
    if expected_revision == 0:
        matches_revision: dict = {"$or": [{"chat_capsule": None}, {"chat_capsule.revision": 0}]}
    else:
        matches_revision = {"chat_capsule.revision": expected_revision}
    revision = expected_revision + 1
    result = await db.projects.update_one(
        {"_id": project_id, "active_run": run_id, **matches_revision},
        {"$set": {"chat_capsule": {
            "text": text, "covered_ids": covered_ids, "source_ids": list(source_ids or []), "revision": revision,
        }}},
    )
    if not result.matched_count:
        raise ContextError("CAPSULE_CONFLICT")
    return revision


async def get_run(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, run_id: str) -> dict:
    run = await db.ai_runs.find_one({"_id": parse_object_id(run_id), "user_id": user_id, "project_id": project_id})
    if run is None:
        raise not_found()
    return run
