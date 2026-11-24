from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo.errors import WriteError


async def insert_job(db, user_id):
    now = datetime.now(timezone.utc)
    result = await db.ai_jobs.insert_one(
        {
            "user_id": ObjectId(user_id),
            "brand_id": ObjectId(),
            "job_type": "ck_01",
            "input": {"prompt": "초록색 로고", "file_id": ObjectId()},
            "status": "queued",
            "created_at": now,
            "updated_at": now,
        }
    )
    return str(result.inserted_id)


async def test_jobs_list_only_mine(client, login, db):
    other = await login(client, email="b@test.com")
    await insert_job(db, other["id"])
    me = await login(client)
    job_id = await insert_job(db, me["id"])
    jobs = (await client.get("/jobs")).json()
    assert [job["id"] for job in jobs] == [job_id]
    assert jobs[0]["status"] == "queued"
    assert isinstance(jobs[0]["input"]["file_id"], str)


async def test_job_detail_and_other_users_job_is_404(client, login, db):
    me = await login(client)
    job_id = await insert_job(db, me["id"])
    assert (await client.get(f"/jobs/{job_id}")).json()["job_type"] == "ck_01"
    await login(client, email="b@test.com")
    assert (await client.get(f"/jobs/{job_id}")).status_code == 404
    assert (await client.get("/jobs/not-an-id")).status_code == 404


async def test_jobs_require_login(client):
    assert (await client.get("/jobs")).status_code == 401


async def test_jobs_have_no_write_routes(client, login, db):
    me = await login(client)
    job_id = await insert_job(db, me["id"])
    assert (await client.post("/jobs", json={})).status_code == 405
    assert (await client.patch(f"/jobs/{job_id}", json={})).status_code == 405
    assert (await client.delete(f"/jobs/{job_id}")).status_code == 405


async def test_job_type_is_one_of_the_plan_keys(db, login, client):
    me = await login(client)
    now = datetime.now(timezone.utc)
    job = {"user_id": ObjectId(me["id"]), "input": {}, "status": "queued", "created_at": now, "updated_at": now}
    with pytest.raises(WriteError):
        await db.ai_jobs.insert_one({**job, "job_type": "logo"})
    for key in [f"ck_0{n}" for n in range(1, 7)]:
        await db.ai_jobs.insert_one({**job, "job_type": key})
    assert len((await client.get("/jobs")).json()) == 6


async def test_job_can_belong_to_a_project_without_brand(db, login, client):
    me = await login(client)
    project = (await client.post("/projects", json={"name": "브랜드 없는 상품"})).json()
    now = datetime.now(timezone.utc)
    result = await db.ai_jobs.insert_one(
        {
            "user_id": ObjectId(me["id"]),
            "brand_id": None,
            "project_id": ObjectId(project["id"]),
            "job_type": "ck_02",
            "input": {},
            "status": "queued",
            "created_at": now,
            "updated_at": now,
        }
    )
    job = (await client.get(f"/jobs/{result.inserted_id}")).json()
    assert (job["brand_id"], job["project_id"], job["job_type"]) == (None, project["id"], "ck_02")
