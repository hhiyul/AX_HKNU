"""AI 채팅 실행 기록과 잠금(services/ai_runs.py) 테스트."""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.ai.context import ContextError
from app.errors import AppError
from app.services import ai_runs
from app.services.ai_runs import RunCheck
from app.services.store import utcnow


@pytest.fixture
async def project(client, login, db):
    me = await login(client)
    created = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return {"user_id": ObjectId(me["id"]), "project_id": ObjectId(created["id"])}


async def active_run(db, project_id):
    return (await db.projects.find_one({"_id": project_id})).get("active_run")


async def test_begin_run_locks_project(db, project):
    before = (await db.projects.find_one({"_id": project["project_id"]}))["revision"]
    run_id = await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    assert await active_run(db, project["project_id"]) == run_id
    run = await db.ai_runs.find_one({"_id": run_id})
    assert run["status"] == "running" and run["heartbeat_at"] is not None
    with pytest.raises(AppError) as caught:
        await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    assert (caught.value.status, caught.value.code) == (409, "RUN_ACTIVE")
    assert (await db.projects.find_one({"_id": project["project_id"]}))["revision"] == before


async def test_attach_user_message(db, project):
    run_id = await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    message_id = ObjectId()
    await ai_runs.attach_user_message(db, run_id, message_id)
    assert (await db.ai_runs.find_one({"_id": run_id}))["user_message_id"] == message_id


async def test_finish_run_releases_lock_and_is_idempotent(db, project):
    run_id = await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    usage = {"generation": {"total_tokens": 3}}
    await ai_runs.finish_run(db, run_id, project["project_id"], "completed", usage=usage)
    assert await active_run(db, project["project_id"]) is None
    await ai_runs.finish_run(db, run_id, project["project_id"], "failed", "LATE")
    run = await db.ai_runs.find_one({"_id": run_id})
    assert (run["status"], run.get("error_code"), run["usage"]) == ("completed", None, usage)
    assert run["finished_at"] is not None


async def test_check_states(db, project):
    pid = project["project_id"]
    run_id = await ai_runs.begin_run(db, project["user_id"], pid)
    assert await ai_runs.check(db, run_id, pid) == RunCheck.OK
    await ai_runs.request_stop(db, run_id)
    assert await ai_runs.check(db, run_id, pid) == RunCheck.STOP_REQUESTED
    await db.ai_runs.update_one({"_id": run_id}, {"$set": {"stop_requested": False}})
    await db.projects.update_one({"_id": pid}, {"$set": {"deleted_at": utcnow()}})
    assert await ai_runs.check(db, run_id, pid) == RunCheck.PROJECT_NOT_ACTIVE
    await db.projects.update_one({"_id": pid}, {"$set": {"deleted_at": None}})
    await ai_runs.finish_run(db, run_id, pid, "completed")
    assert await ai_runs.check(db, run_id, pid) == RunCheck.LOST


async def test_request_stop_ignores_finished_run(db, project):
    run_id = await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    await ai_runs.finish_run(db, run_id, project["project_id"], "completed")
    await ai_runs.request_stop(db, run_id)
    assert not (await db.ai_runs.find_one({"_id": run_id})).get("stop_requested")


async def test_reap_stale_only_touches_dead_runs(db, client, login, project):
    other = (await client.post("/projects", json={"name": "다른 프로젝트"})).json()
    other_id = ObjectId(other["id"])
    dead = await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    alive = await ai_runs.begin_run(db, project["user_id"], other_id)
    now = utcnow()
    await db.ai_runs.update_one({"_id": dead}, {"$set": {"heartbeat_at": now - timedelta(seconds=61)}})
    await db.ai_runs.update_one({"_id": alive}, {"$set": {"heartbeat_at": now - timedelta(seconds=10)}})
    assert await ai_runs.reap_stale(db) == 1
    run = await db.ai_runs.find_one({"_id": dead})
    assert (run["status"], run["error_code"], run["delivery"]) == ("unknown", "PROCESS_RESTART", "unknown")
    assert await active_run(db, project["project_id"]) is None
    assert (await db.ai_runs.find_one({"_id": alive}))["status"] == "running"
    assert await active_run(db, other_id) == alive


async def test_heartbeat_keeps_run_alive(db, project):
    run_id = await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    old = utcnow() - timedelta(seconds=61)
    await db.ai_runs.update_one({"_id": run_id}, {"$set": {"heartbeat_at": old}})
    await ai_runs.heartbeat(db, run_id)
    assert await ai_runs.reap_stale(db) == 0


async def test_begin_run_reaps_dead_lock(db, project):
    dead = await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    stale = utcnow() - timedelta(seconds=61)
    await db.ai_runs.update_one({"_id": dead}, {"$set": {"heartbeat_at": stale}})
    run_id = await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    assert await active_run(db, project["project_id"]) == run_id
    assert (await db.ai_runs.find_one({"_id": dead}))["status"] == "unknown"


async def test_begin_run_refuses_inactive_project(db, project):
    await db.projects.update_one({"_id": project["project_id"]}, {"$set": {"archived_at": utcnow()}})
    with pytest.raises(AppError) as caught:
        await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    assert caught.value.code == "PROJECT_NOT_ACTIVE"


async def test_capsule_conditional_save(db, project):
    pid = project["project_id"]
    before = (await db.projects.find_one({"_id": pid}))["revision"]
    run_id = await ai_runs.begin_run(db, project["user_id"], pid)
    assert await ai_runs.load_capsule(db, pid) is None
    assert await ai_runs.save_capsule(db, pid, run_id, 0, "요약", ["a", "b"]) == 1
    assert await ai_runs.load_capsule(db, pid) == {"text": "요약", "covered_ids": ["a", "b"], "source_ids": [], "revision": 1}
    with pytest.raises(ContextError, match="CAPSULE_CONFLICT"):
        await ai_runs.save_capsule(db, pid, run_id, 0, "늦은 요약", ["a"])
    with pytest.raises(ContextError, match="CAPSULE_CONFLICT"):
        await ai_runs.save_capsule(db, pid, ObjectId(), 1, "다른 실행", ["a"])
    assert (await db.projects.find_one({"_id": pid}))["revision"] == before


async def test_get_run_checks_owner_and_project(db, project):
    run_id = await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    run = await ai_runs.get_run(db, project["user_id"], project["project_id"], str(run_id))
    assert run["_id"] == run_id
    for user_id, project_id, ident in [
        (ObjectId(), project["project_id"], str(run_id)),
        (project["user_id"], ObjectId(), str(run_id)),
        (project["user_id"], project["project_id"], "not-an-id"),
    ]:
        with pytest.raises(AppError) as caught:
            await ai_runs.get_run(db, user_id, project_id, ident)
        assert caught.value.status == 404


async def test_begin_run_clears_orphaned_lock(db, project):
    # 실행은 끝났는데 잠금 해제(두 번째 쓰기)가 안 된 경우
    old = await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    await db.ai_runs.update_one({"_id": old}, {"$set": {"status": "completed"}})
    run_id = await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    assert await active_run(db, project["project_id"]) == run_id


async def test_begin_run_clears_lock_to_missing_run(db, project):
    await db.projects.update_one({"_id": project["project_id"]}, {"$set": {"active_run": ObjectId()}})
    run_id = await ai_runs.begin_run(db, project["user_id"], project["project_id"])
    assert await active_run(db, project["project_id"]) == run_id
