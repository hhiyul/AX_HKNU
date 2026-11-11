from bson import ObjectId


async def create_brand(client):
    return (await client.post("/brands", json={"brand_type": "new"})).json()


async def create_project(client, name="핫팩 파우치", **fields):
    res = await client.post("/projects", json={"name": name, **fields})
    assert res.status_code == 201, res.text
    return res.json()


async def test_project_needs_only_a_name(client, login):
    me = await login(client)
    assert (await client.post("/projects", json={})).status_code == 422
    assert (await client.post("/projects", json={"name": "   "})).status_code == 422
    project = await create_project(client)
    assert project["user_id"] == me["id"]
    assert project["brand_id"] is None and project["description"] is None
    assert project["current_step"] == "overview"
    assert project["revision"] == 1


async def test_project_brand_must_be_mine(client, login):
    await login(client)
    brand = await create_brand(client)
    assert (await create_project(client, brand_id=brand["id"]))["brand_id"] == brand["id"]
    assert (await client.post("/projects", json={"name": "x", "brand_id": str(ObjectId())})).status_code == 404
    await login(client, email="b@test.com")
    assert (await client.post("/projects", json={"name": "x", "brand_id": brand["id"]})).status_code == 404


async def test_old_fields_are_rejected(client, login):
    await login(client)
    for field in ({"features": []}, {"expected_price": 1000}, {"status": "draft"}, {"target_customer": "x"}):
        assert (await client.post("/projects", json={"name": "x", **field})).status_code == 422, field


async def test_patch_needs_current_revision(client, login):
    await login(client)
    project = await create_project(client)
    url = f"/projects/{project['id']}"
    assert (await client.patch(url, json={"name": "새 이름"})).status_code == 422
    res = await client.patch(url, json={"name": "새 이름", "current_step": "plan", "revision": 1})
    assert res.status_code == 200
    updated = res.json()
    assert (updated["name"], updated["current_step"], updated["revision"]) == ("새 이름", "plan", 2)
    # 다른 탭이 예전 내용을 보고 저장하면 덮어쓰지 않고 막는다.
    stale = await client.patch(url, json={"name": "예전 탭", "revision": 1})
    assert stale.status_code == 409
    assert stale.json()["code"] == "REVISION_CONFLICT"
    assert (await client.get(url)).json()["name"] == "새 이름"


async def test_brand_can_be_linked_and_unlinked(client, login):
    await login(client)
    brand = await create_brand(client)
    project = await create_project(client)
    url = f"/projects/{project['id']}"
    linked = (await client.patch(url, json={"brand_id": brand["id"], "revision": 1})).json()
    assert linked["brand_id"] == brand["id"]
    unlinked = (await client.patch(url, json={"brand_id": None, "revision": 2})).json()
    assert unlinked["brand_id"] is None
    assert (await client.patch(url, json={"name": None, "revision": 3})).status_code == 422


async def test_list_projects(client, login):
    await login(client)
    brand = await create_brand(client)
    one = await create_project(client, "첫째", brand_id=brand["id"])
    two = await create_project(client, "둘째")
    assert [item["id"] for item in (await client.get("/projects")).json()] == [two["id"], one["id"]]
    filtered = (await client.get("/projects", params={"brand_id": brand["id"]})).json()
    assert [item["id"] for item in filtered] == [one["id"]]
    await login(client, email="b@test.com")
    assert (await client.get("/projects")).json() == []
    assert (await client.get("/projects", params={"brand_id": brand["id"]})).status_code == 404


async def test_other_users_project_is_404(client, login):
    await login(client)
    project = await create_project(client)
    await login(client, email="b@test.com")
    url = f"/projects/{project['id']}"
    assert (await client.get(url)).status_code == 404
    assert (await client.patch(url, json={"name": "x", "revision": 1})).status_code == 404
    assert (await client.delete(url)).status_code == 404
    for path in ("plan", "record", "economics/costs", "documents", "chat", "proposals", "overview", "projection"):
        assert (await client.get(f"{url}/{path}")).status_code == 404, path


async def test_invalid_project_id_is_404(client, login):
    await login(client)
    assert (await client.get("/projects/not-an-id")).status_code == 404


async def test_delete_project_removes_everything_under_it(client, login, db):
    await login(client)
    project = await create_project(client)
    other = await create_project(client, "다른 프로젝트")
    url = f"/projects/{project['id']}"
    await client.put(f"{url}/plan/ck_01", json={"kind": "DECIDED", "content": "핫팩", "revision": 0})
    await client.post(f"{url}/record/entries", json={"title": "메모", "content": "내용", "revision": 0})
    await client.put(f"{url}/economics/costs", json={"items": [{"label": "원단", "amount": 3000}], "revision": 0})
    await client.post(f"{url}/economics/scenarios", json={"price": 10000})
    doc = (await client.post(f"{url}/documents", json={"doc_type": "PLAN", "title": "기획서"})).json()
    await client.post(f"{url}/documents/{doc['id']}/finalize", json={"revision": 1})
    await client.post(f"{url}/chat/messages", json={"content": "안녕"})
    await client.post(f"{url}/proposals", json={"target": "ck_02", "content": "20대 직장인"})
    # 다른 프로젝트가 이 프로젝트를 참고 자료로 걸어 둔 경우
    other_chat = f"/projects/{other['id']}/chat"
    await client.get(other_chat)
    res = await client.patch(other_chat, json={"chip_refs": [project["id"]], "revision": 1})
    assert res.status_code == 200, res.text

    assert (await client.delete(url)).status_code == 204
    for name in (
        "plan_items", "plan_versions", "records", "cost_sheets", "scenarios",
        "documents", "document_versions", "proposals", "messages",
    ):
        assert await db[name].count_documents({}) == 0, name
    assert (await client.get(other_chat)).json()["chip_refs"] == []
    assert (await client.get(url)).status_code == 404
