from datetime import datetime, timezone

import pytest
from bson import ObjectId

PNG = b"\x89PNG\r\n\x1a\n" + b"image-data"


async def create_brand(client):
    return (await client.post("/brands", json={"brand_type": "new"})).json()


async def upload(client, brand_id, content=PNG, name="a.png", mime="image/png", asset_type="logo"):
    return await client.post(
        "/assets",
        data={"brand_id": brand_id, "asset_type": asset_type},
        files={"file": (name, content, mime)},
    )


async def insert_job(db, user_id, brand_id, status, project_id=None):
    now = datetime.now(timezone.utc)
    result = await db.ai_jobs.insert_one(
        {
            "user_id": ObjectId(user_id),
            "brand_id": ObjectId(brand_id),
            "project_id": ObjectId(project_id) if project_id else None,
            "job_type": "ck_01",
            "input": {},
            "status": status,
            "created_at": now,
            "updated_at": now,
        }
    )
    return result.inserted_id


# --- 파일 형식 ---


@pytest.mark.parametrize("asset_type", ["logo", "banner", "product_image"])
async def test_image_asset_types_reject_text(client, login, asset_type):
    await login(client)
    brand = await create_brand(client)
    res = await upload(client, brand["id"], "메모".encode(), "a.txt", "text/plain", asset_type)
    assert res.status_code == 422
    assert res.json()["code"] == "INVALID_ASSET"


async def test_logo_must_be_image(client, login):
    await login(client)
    brand = await create_brand(client)
    memo = (await upload(client, brand["id"], "메모".encode(), "a.txt", "text/plain", "document")).json()
    res = await client.patch(f"/brands/{brand['id']}", json={"logo_asset_id": memo["id"]})
    assert res.status_code == 422
    assert res.json()["code"] == "LOGO_NOT_IMAGE"
    image = (await upload(client, brand["id"], asset_type="product_image")).json()
    res = await client.patch(f"/brands/{brand['id']}", json={"logo_asset_id": image["id"]})
    assert res.status_code == 200


# --- 삭제와 ai_jobs ---


@pytest.mark.parametrize("status", ["queued", "running"])
async def test_delete_brand_blocked_by_active_job(client, login, db, status):
    me = await login(client)
    brand = await create_brand(client)
    job_id = await insert_job(db, me["id"], brand["id"], status)
    res = await client.delete(f"/brands/{brand['id']}")
    assert res.status_code == 409
    assert res.json()["code"] == "BRAND_IN_USE"
    assert await db.ai_jobs.find_one({"_id": job_id})


async def test_delete_brand_removes_finished_jobs(client, login, db):
    me = await login(client)
    brand, other = await create_brand(client), await create_brand(client)
    await insert_job(db, me["id"], brand["id"], "succeeded")
    await insert_job(db, me["id"], brand["id"], "failed")
    kept = await insert_job(db, me["id"], other["id"], "succeeded")
    assert (await client.delete(f"/brands/{brand['id']}")).status_code == 204
    assert [job["_id"] async for job in db.ai_jobs.find({})] == [kept]


async def test_delete_project_handles_jobs(client, login, db):
    me = await login(client)
    brand = await create_brand(client)
    project = (await client.post("/projects", json={"name": "상품", "brand_id": brand["id"]})).json()
    active = await insert_job(db, me["id"], brand["id"], "running", project["id"])
    await insert_job(db, me["id"], brand["id"], "succeeded", project["id"])
    brand_only = await insert_job(db, me["id"], brand["id"], "succeeded")
    # 휴지통으로 옮기는 것은 막지 않고, 완전히 지울 때만 진행 중인 작업을 확인한다.
    assert (await client.delete(f"/projects/{project['id']}")).status_code == 204
    res = await client.delete(f"/projects/{project['id']}", params={"permanent": "true"})
    assert res.status_code == 409
    assert res.json()["code"] == "PROJECT_IN_USE"
    await db.ai_jobs.update_one({"_id": active}, {"$set": {"status": "failed"}})
    assert (await client.delete(f"/projects/{project['id']}", params={"permanent": "true"})).status_code == 204
    assert [job["_id"] async for job in db.ai_jobs.find({})] == [brand_only]


# --- 크기 제한 ---


@pytest.mark.parametrize("field", ["keywords", "colors"])
async def test_brand_list_item_count_limit(client, login, field):
    await login(client)
    assert (await client.post("/brands", json={"brand_type": "new", field: ["x"] * 50})).status_code == 201
    assert (await client.post("/brands", json={"brand_type": "new", field: ["x"] * 51})).status_code == 422
    brand = await create_brand(client)
    assert (await client.patch(f"/brands/{brand['id']}", json={field: ["x"] * 51})).status_code == 422


async def test_lists_are_paged(client, login, db):
    me = await login(client)
    brands = [await create_brand(client) for _ in range(3)]
    newest_first = [brand["id"] for brand in reversed(brands)]
    listed = (await client.get("/brands", params={"limit": 2})).json()
    assert [brand["id"] for brand in listed] == newest_first[:2]
    listed = (await client.get("/brands", params={"limit": 2, "skip": 2})).json()
    assert [brand["id"] for brand in listed] == newest_first[2:]

    brand_id = brands[0]["id"]
    for _ in range(3):
        await client.post("/projects", json={"name": "상품", "brand_id": brand_id})
        await upload(client, brand_id)
        await insert_job(db, me["id"], brand_id, "succeeded")
    assert len((await client.get("/projects", params={"limit": 2})).json()) == 2
    assert len((await client.get("/assets", params={"brand_id": brand_id, "limit": 2})).json()) == 2
    assert len((await client.get("/jobs", params={"limit": 2})).json()) == 2
    assert len((await client.get("/jobs", params={"skip": 2})).json()) == 1


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"skip": -1}])
async def test_page_params_are_validated(client, login, params):
    await login(client)
    assert (await client.get("/brands", params=params)).status_code == 422
