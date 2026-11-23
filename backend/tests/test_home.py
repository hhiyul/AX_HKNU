"""v05 계정 홈: 최근 문서·오늘 할 일·기한 지난 일·진행 중 문서·규칙 기반 추천."""
import pytest

TODAY = "2026-11-26"


@pytest.fixture
async def world(client, login):
    await login(client)
    brand = (await client.post("/brands", json={"brand_type": "new", "name": "칠보공방"})).json()
    branded = (await client.post("/projects", json={"name": "칠보 팔찌", "brand_id": brand["id"]})).json()
    plain = (await client.post("/projects", json={"name": "브랜드 없는 상품"})).json()
    a, b = f"/projects/{branded['id']}", f"/projects/{plain['id']}"
    funding = (await client.post(f"{a}/docs", json={"template_id": "funding", "title": "팔찌 펀딩"})).json()
    plan = (await client.post(f"{b}/docs", json={"template_id": "business_plan", "title": "사업계획서"})).json()
    await client.put(f"{a}/docs/{funding['id']}/sections/F05", json={"text": "35,000원", "revision": 0})
    tasks = {}
    for name, url, date in [("today", a, TODAY), ("overdue", a, "2026-11-20"), ("future", b, "2026-12-10"),
                            ("other_today", b, TODAY)]:
        tasks[name] = (await client.post(f"{url}/tasks", json={"title": name, "date": date})).json()
    done = (await client.post(f"{a}/tasks", json={"title": "done", "date": TODAY})).json()
    await client.patch(f"{a}/tasks/{done['id']}", json={"done": True, "revision": done["revision"]})
    return {"client": client, "brand_id": brand["id"], "a": a, "b": b, "funding": funding, "plan": plan,
            "branded": branded, "plain": plain, "tasks": tasks}


async def home(world, **params):
    res = await world["client"].get("/home", params={"today": TODAY, **params})
    assert res.status_code == 200, res.text
    return res.json()


async def test_home_collects_account_wide_state(world):
    data = await home(world)
    assert [d["title"] for d in data["recent_docs"]] == ["팔찌 펀딩", "사업계획서"]
    recent = data["recent_docs"][0]
    assert (recent["project_name"], recent["last_section_id"], recent["last_section_title"]) == (
        "칠보 팔찌", "F05", "리워드·가격·수량"
    )
    assert recent["progress"]["required_total"] == 6
    assert sorted(t["title"] for t in data["today_tasks"]) == ["other_today", "today"]
    assert all(t["project_name"] for t in data["today_tasks"])
    assert [t["title"] for t in data["overdue_tasks"]] == ["overdue"]
    assert {d["title"] for d in data["docs_in_progress"]} == {"팔찌 펀딩", "사업계획서"}
    kinds = {r["kind"] for r in data["recommendations"]}
    assert {"overdue_task", "required_section"} <= kinds
    overdue = next(r for r in data["recommendations"] if r["kind"] == "overdue_task")
    assert overdue["task_id"] == world["tasks"]["overdue"]["id"]
    section = next(r for r in data["recommendations"] if r["kind"] == "required_section")
    assert section["doc_id"] and section["section_id"] and section["project_id"]


async def test_home_brand_scope(world):
    branded = await home(world, brand_id=world["brand_id"])
    assert [d["title"] for d in branded["recent_docs"]] == ["팔찌 펀딩"]
    assert [t["title"] for t in branded["today_tasks"]] == ["today"]
    none = await home(world, brand_id="none")
    assert [d["title"] for d in none["recent_docs"]] == ["사업계획서"]
    assert [t["title"] for t in none["today_tasks"]] == ["other_today"]


async def test_home_skips_trashed_and_archived(world):
    client = world["client"]
    await client.delete(f"{world['a']}/docs/{world['funding']['id']}")
    await client.delete(f"{world['b']}/tasks/{world['tasks']['other_today']['id']}")
    revision = (await client.get(world["b"])).json()["revision"]
    await client.patch(world["b"], json={"status": "archived", "revision": revision})
    data = await home(world)
    assert data["recent_docs"] == [] and data["docs_in_progress"] == []
    assert [t["title"] for t in data["today_tasks"]] == ["today"]


async def test_finished_docs_are_not_in_progress(world):
    client, a, doc_id = world["client"], world["a"], world["funding"]["id"]
    for section in ["F01", "F02", "F04", "F05", "F06", "F08"]:
        current = next(s for s in (await client.get(f"{a}/docs/{doc_id}")).json()["sections"] if s["section_id"] == section)
        saved = (await client.put(f"{a}/docs/{doc_id}/sections/{section}",
                                  json={"text": "내용", "revision": current["revision"]})).json()
        await client.post(f"{a}/docs/{doc_id}/sections/{section}/review", json={"revision": saved["revision"]})
    data = await home(world)
    assert [d["title"] for d in data["docs_in_progress"]] == ["사업계획서"]
    assert not any(r.get("doc_id") == doc_id and r["kind"] == "required_section" for r in data["recommendations"])


async def test_home_is_per_user_and_needs_login(world, login):
    await login(world["client"], email="b@test.com")
    data = await home(world)
    assert data["recent_docs"] == [] and data["today_tasks"] == [] and data["recommendations"] == []
    assert (await world["client"].get("/home", params={"today": "2026-13-40"})).status_code == 422


async def test_home_without_today_uses_korean_date(world):
    assert (await world["client"].get("/home")).status_code == 200
