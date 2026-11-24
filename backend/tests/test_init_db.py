"""init_db.py의 예전 문서 보정(migrate) 테스트."""
import os
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import WriteError

import init_db


def test_migrate_fills_missing_plan_items_as_skipped(db):
    now = datetime.now(timezone.utc)
    user_id, project_id = ObjectId(), ObjectId()
    with MongoClient(os.environ["MONGO_DETAILS"]) as client:
        sync_db = client.get_default_database()
        sync_db.projects.insert_one(
            {"_id": project_id, "user_id": user_id, "name": "예전 프로젝트", "current_step": "overview",
             "revision": 1, "plan_filled": 1, "created_at": now, "updated_at": now}
        )
        sync_db["items"].insert_one(
            {"user_id": user_id, "project_id": project_id, "kind": "plan", "key": "ck_01", "content": "칠보 팔찌",
             "revision": 3, "created_at": now, "updated_at": now}
        )
        init_db.migrate(sync_db)
        init_db.migrate(sync_db)
        plan = {doc["key"]: doc for doc in sync_db["items"].find({"project_id": project_id, "kind": "plan"})}
    assert sorted(plan) == [f"ck_0{n}" for n in range(1, 7)]
    assert (plan["ck_01"]["content"], plan["ck_01"]["revision"]) == ("칠보 팔찌", 3)
    assert (plan["ck_02"]["content"], plan["ck_02"]["revision"], plan["ck_02"]["user_id"]) == ("", 1, user_id)


async def test_ai_runs_rules(db):
    now = datetime.now(timezone.utc)
    run = {"user_id": ObjectId(), "project_id": ObjectId(), "created_at": now, "updated_at": now}
    with pytest.raises(WriteError):
        await db.ai_runs.insert_one({**run, "status": "queued"})
    await db.ai_runs.insert_one({**run, "status": "running", "user_message_id": None})
