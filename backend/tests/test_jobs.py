from datetime import datetime, timezone

from bson import ObjectId


async def insert_job(db, user_id):
    now = datetime.now(timezone.utc)
    result = await db.ai_jobs.insert_one(
        {
            "user_id": ObjectId(user_id),
            "brand_id": ObjectId(),
            "job_type": "logo",
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
    assert (await client.get(f"/jobs/{job_id}")).json()["job_type"] == "logo"
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
