from bson import ObjectId


async def create_brand(client):
    return (await client.post("/brands", json={"brand_type": "new"})).json()


async def create_project(client, brand_id, **fields):
    res = await client.post("/projects", json={"brand_id": brand_id, **fields})
    assert res.status_code == 201, res.text
    return res.json()


async def test_project_requires_owned_brand(client, login):
    await login(client)
    brand = await create_brand(client)
    assert (await client.post("/projects", json={"name": "상품"})).status_code == 422
    assert (await client.post("/projects", json={"brand_id": str(ObjectId())})).status_code == 404
    await login(client, email="b@test.com")
    assert (await client.post("/projects", json={"brand_id": brand["id"]})).status_code == 404


async def test_project_defaults(client, login):
    await login(client)
    project = await create_project(client, (await create_brand(client))["id"])
    assert project["status"] == "draft"
    assert project["current_step"] == "basic"
    assert project["features"] == []
    assert project["name"] is None and project["expected_price"] is None


async def test_expected_price_values(client, login, db):
    await login(client)
    brand_id = (await create_brand(client))["id"]
    for price in (0, 15000, 10**12):
        project = await create_project(client, brand_id, expected_price=price)
        assert project["expected_price"] == price
    saved = await db.projects.find_one({"_id": ObjectId(project["id"])})
    assert saved["expected_price"] == 10**12
    for price in (-1, "1000", 2**63, 1.5, True):
        res = await client.post("/projects", json={"brand_id": brand_id, "expected_price": price})
        assert res.status_code == 422, price


async def test_project_patch_saves_basic_info_step_by_step(client, login):
    await login(client)
    project = await create_project(client, (await create_brand(client))["id"])
    url = f"/projects/{project['id']}"
    assert (await client.patch(url, json={"name": "핫팩 파우치"})).status_code == 200
    res = await client.patch(url, json={"features": ["경량"], "current_step": "price", "expected_price": 19000})
    updated = res.json()
    assert updated["name"] == "핫팩 파우치"
    assert updated["features"] == ["경량"]
    assert updated["current_step"] == "price"
    assert updated["expected_price"] == 19000
    assert (await client.patch(url, json={"expected_price": None})).json()["expected_price"] is None


async def test_project_null_not_allowed_fields_return_422(client, login):
    await login(client)
    project = await create_project(client, (await create_brand(client))["id"])
    for field in ("features", "current_step", "status"):
        res = await client.patch(f"/projects/{project['id']}", json={field: None})
        assert res.status_code == 422, field


async def test_project_completed_requires_name(client, login):
    await login(client)
    project = await create_project(client, (await create_brand(client))["id"])
    url = f"/projects/{project['id']}"
    res = await client.patch(url, json={"status": "completed"})
    assert res.status_code == 422
    assert res.json()["code"] == "NAME_REQUIRED"
    assert (await client.patch(url, json={"status": "completed", "name": "상품"})).status_code == 200


async def test_project_brand_id_cannot_be_patched(client, login):
    await login(client)
    brand = await create_brand(client)
    project = await create_project(client, brand["id"])
    other = await create_brand(client)
    assert (await client.patch(f"/projects/{project['id']}", json={"brand_id": other["id"]})).status_code == 422


async def test_list_projects_by_brand(client, login):
    await login(client)
    first, second = await create_brand(client), await create_brand(client)
    one = await create_project(client, first["id"])
    await create_project(client, second["id"])
    assert len((await client.get("/projects")).json()) == 2
    filtered = (await client.get("/projects", params={"brand_id": first["id"]})).json()
    assert [item["id"] for item in filtered] == [one["id"]]
    await login(client, email="b@test.com")
    assert (await client.get("/projects")).json() == []
    assert (await client.get("/projects", params={"brand_id": first["id"]})).status_code == 404


async def test_other_users_project_is_404(client, login):
    await login(client)
    project = await create_project(client, (await create_brand(client))["id"])
    await login(client, email="b@test.com")
    url = f"/projects/{project['id']}"
    assert (await client.get(url)).status_code == 404
    assert (await client.patch(url, json={"name": "x"})).status_code == 404
    assert (await client.delete(url)).status_code == 404


async def test_invalid_project_id_is_404(client, login):
    await login(client)
    assert (await client.get("/projects/not-an-id")).status_code == 404
