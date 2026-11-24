from datetime import datetime, timedelta, timezone

from bson import ObjectId


async def create_brand(client, **fields):
    return (await client.post("/brands", json=fields)).json()


async def create_project(client, name="칠보 팔찌", **fields):
    res = await client.post("/projects", json={"name": name, **fields})
    assert res.status_code == 201, res.text
    return res.json()


async def listed(client, **params):
    res = await client.get("/projects", params=params)
    assert res.status_code == 200, res.text
    return [item["id"] for item in res.json()]


async def test_project_needs_only_a_name(client, login):
    me = await login(client)
    assert (await client.post("/projects", json={})).status_code == 422
    assert (await client.post("/projects", json={"name": "   "})).status_code == 422
    project = await create_project(client)
    assert project["user_id"] == me["id"]
    assert project["brand_id"] is None and project["description"] is None
    assert project["current_step"] == "overview"
    assert (project["stage"], project["status"], project["revision"]) == ("planning", "active", 1)
    assert (project["note"], project["next_action"], project["plan_filled"]) == (None, None, 0)
    assert [entry["text"] for entry in project["recent"]] == ["프로젝트 생성"]


async def test_project_brand_must_be_mine(client, login):
    await login(client)
    brand = await create_brand(client)
    assert (await create_project(client, brand_id=brand["id"]))["brand_id"] == brand["id"]
    assert (await client.post("/projects", json={"name": "x", "brand_id": str(ObjectId())})).status_code == 404
    await login(client, email="b@test.com")
    assert (await client.post("/projects", json={"name": "x", "brand_id": brand["id"]})).status_code == 404


async def test_unknown_fields_are_rejected(client, login):
    await login(client)
    for field in ({"features": []}, {"expected_price": 1000}, {"status": "active"}, {"stage": "done"}):
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


async def test_stage_and_note(client, login):
    await login(client)
    project = await create_project(client)
    url = f"/projects/{project['id']}"
    staged = (await client.patch(url, json={"stage": "prototype", "revision": 1})).json()
    assert staged["stage"] == "prototype"
    assert staged["recent"][0]["text"] == "진행 단계 변경 · 기획 → 시제품"
    noted = (await client.patch(url, json={"note": "체인 단가 확인", "revision": 2})).json()
    assert noted["note"] == "체인 단가 확인"
    assert (await client.patch(url, json={"note": "  ", "revision": 3})).json()["note"] is None
    assert (await client.patch(url, json={"stage": "done", "revision": 4})).status_code == 422
    assert (await client.patch(url, json={"stage": None, "revision": 4})).status_code == 422
    # 다음 할 일은 AI 파이프라인이 채우는 값이라 직접 바꿀 수 없다.
    assert (await client.patch(url, json={"next_action": "x", "revision": 4})).status_code == 422


async def test_list_projects_by_brand_and_search(client, login):
    await login(client)
    brand = await create_brand(client)
    one = await create_project(client, "달빛 칠보 팔찌", brand_id=brand["id"])
    two = await create_project(client, "린넨 미니 토트")
    assert await listed(client) == [two["id"], one["id"]]
    assert await listed(client, brand_id=brand["id"]) == [one["id"]]
    assert await listed(client, brand_id="none") == [two["id"]]
    assert await listed(client, q="칠보") == [one["id"]]
    assert await listed(client, q="LINEN") == []
    # 검색어의 특수 문자는 그대로 글자로 본다.
    assert await listed(client, q=".*") == []
    await login(client, email="b@test.com")
    assert await listed(client) == []
    assert (await client.get("/projects", params={"brand_id": brand["id"]})).status_code == 404


async def test_archive_trash_and_restore(client, login):
    await login(client)
    kept = await create_project(client, "진행 중")
    project = await create_project(client, "정리할 프로젝트")
    url = f"/projects/{project['id']}"
    await client.put(f"{url}/items/plan/ck_01", json={"content": "칠보 팔찌", "revision": 1})

    archived = (await client.patch(url, json={"status": "archived", "revision": 1})).json()
    assert archived["status"] == "archived" and archived["archived_at"]
    assert await listed(client) == [kept["id"]]
    assert await listed(client, status="archived") == [project["id"]]
    # 보관함·휴지통에 있는 동안에는 작업실을 열 수 없다.
    blocked = await client.get(f"{url}/items/plan")
    assert blocked.status_code == 409 and blocked.json()["code"] == "PROJECT_NOT_ACTIVE"

    assert (await client.delete(url)).status_code == 204
    trashed = (await client.get(url)).json()
    assert trashed["status"] == "trash" and trashed["revision"] == 3
    assert await listed(client, status="archived") == []
    assert await listed(client, status="trash") == [project["id"]]

    restored = (await client.patch(url, json={"status": "active", "revision": 3})).json()
    assert (restored["status"], restored["archived_at"], restored["deleted_at"]) == ("active", None, None)
    assert restored["recent"][0]["text"] == "휴지통에서 복원"
    # 자료는 그대로 돌아온다.
    assert (await client.get(f"{url}/items/plan/ck_01")).json()["content"] == "칠보 팔찌"
    assert (await client.get("/projects", params={"status": "gone"})).status_code == 422


async def test_other_users_project_is_404(client, login):
    await login(client)
    project = await create_project(client)
    await login(client, email="b@test.com")
    url = f"/projects/{project['id']}"
    assert (await client.get(url)).status_code == 404
    assert (await client.patch(url, json={"name": "x", "revision": 1})).status_code == 404
    assert (await client.delete(url)).status_code == 404
    for path in ("items/plan", "items/record", "economics", "messages", "overview", "projection"):
        assert (await client.get(f"{url}/{path}")).status_code == 404, path


async def test_invalid_project_id_is_404(client, login):
    await login(client)
    assert (await client.get("/projects/not-an-id")).status_code == 404


async def test_permanent_delete_removes_everything_under_it(client, login, db):
    await login(client)
    project = await create_project(client)
    other = await create_project(client, "다른 프로젝트")
    url = f"/projects/{project['id']}"
    await client.put(f"{url}/items/plan/ck_01", json={"content": "칠보 팔찌", "revision": 1})
    await client.post(f"{url}/items/record", json={"title": "정리", "content": "내용"})
    await client.post(f"{url}/items/document", json={"title": "기획서", "content": "본문"})
    await client.put(f"{url}/economics", json={"cost": {"material": 3000}, "revision": 0})
    await client.post(f"{url}/messages", json={"content": "안녕"})
    # 다른 프로젝트가 이 프로젝트를 참고 자료로 걸어 둔 경우
    other_url = f"/projects/{other['id']}"
    res = await client.patch(other_url, json={"chip_refs": [project["id"]], "revision": 1})
    assert res.status_code == 200, res.text

    # 휴지통을 거치지 않고는 완전히 지울 수 없다.
    res = await client.delete(url, params={"permanent": "true"})
    assert res.status_code == 409 and res.json()["code"] == "NOT_IN_TRASH"
    assert (await client.delete(url)).status_code == 204
    # 휴지통에 있는 동안에는 자료가 그대로다.
    mine = {"project_id": ObjectId(project["id"])}
    # 기획 6개 항목 + 정리 내용 + 산출물
    assert await db["items"].count_documents(mine) == 8
    assert (await client.delete(url, params={"permanent": "true"})).status_code == 204
    for name in ("items", "cost_sheets", "messages"):
        assert await db[name].count_documents(mine) == 0, name
    # 다른 프로젝트의 기획 항목은 남는다.
    assert await db["items"].count_documents({}) == 6
    assert (await client.get(other_url)).json()["chip_refs"] == []
    assert (await client.get(url)).status_code == 404


async def test_permanent_delete_blocked_by_running_ai_run(client, login, db):
    me = await login(client)
    project = await create_project(client)
    url = f"/projects/{project['id']}"
    now = datetime.now(timezone.utc)
    run = {"user_id": ObjectId(me["id"]), "project_id": ObjectId(project["id"]), "status": "running",
           "created_at": now, "updated_at": now}
    run_id = (await db.ai_runs.insert_one(run)).inserted_id
    assert (await client.delete(url)).status_code == 204
    res = await client.delete(url, params={"permanent": "true"})
    assert res.status_code == 409 and res.json()["code"] == "PROJECT_IN_USE"
    await db.ai_runs.update_one({"_id": run_id}, {"$set": {"status": "completed"}})
    assert (await client.delete(url, params={"permanent": "true"})).status_code == 204
    assert await db.ai_runs.count_documents({}) == 0


async def test_permanent_delete_reaps_dead_run(client, login, db):
    me = await login(client)
    project = await create_project(client)
    url = f"/projects/{project['id']}"
    stale = datetime.now(timezone.utc) - timedelta(seconds=61)
    run = {"user_id": ObjectId(me["id"]), "project_id": ObjectId(project["id"]), "status": "running",
           "heartbeat_at": stale, "created_at": stale, "updated_at": stale}
    await db.ai_runs.insert_one(run)
    assert (await client.delete(url)).status_code == 204
    assert (await client.delete(url, params={"permanent": "true"})).status_code == 204
