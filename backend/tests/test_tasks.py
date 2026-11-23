"""v05 할 일(일정)과 AI 할 일 제안: 확인한 것만 등록, 같은 제안은 한 번, 원래 항목으로 복귀."""
import asyncio

import pytest

from ai_fakes import COMPLETED, FakeProvider, answer, tool_call
from app.ai.events import RunEvents
from app.ai.provider import DemoProvider
from app.ai.runtime import Runtime, set_runtime

PROPOSAL = {"tasks": [
    {"title": "제작처에 견적·가능 일정 확인", "date": None, "note": "F06 제작 조건"},
    {"title": "포장 샘플 받기", "date": "2026-12-01", "note": ""},
]}


@pytest.fixture
def use_runtime():
    def install(provider) -> Runtime:
        runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
        set_runtime(runtime)
        return runtime

    yield install
    set_runtime(None)


@pytest.fixture
async def ctx(client, login):
    await login(client)
    project = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    url = f"/projects/{project['id']}"
    doc = (await client.post(f"{url}/docs", json={"template_id": "funding", "title": "팔찌 펀딩"})).json()
    return {"client": client, "url": url, "doc_id": doc["id"], "project_id": project["id"]}


async def propose(ctx, runtime, section="F06", **body):
    res = await ctx["client"].post(f"{ctx['url']}/docs/{ctx['doc_id']}/sections/{section}/task-proposals", json=body)
    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]
    await asyncio.wait_for(runtime.tasks[run_id], 5)
    return run_id


async def test_task_crud(ctx):
    client, url = ctx["client"], ctx["url"]
    res = await client.post(f"{url}/tasks", json={"title": "재료 주문", "date": "2026-11-30", "note": "칠보 유약"})
    assert res.status_code == 201, res.text
    task = res.json()
    assert (task["title"], task["date"], task["done"], task["source"]) == ("재료 주문", "2026-11-30", False, None)
    done = (await client.patch(f"{url}/tasks/{task['id']}", json={"done": True, "revision": task["revision"]})).json()
    assert done["done"] is True
    assert (await client.patch(f"{url}/tasks/{task['id']}", json={"done": False, "revision": task["revision"]})).status_code == 409
    assert [t["id"] for t in (await client.get(f"{url}/tasks")).json()] == [task["id"]]
    assert (await client.post(f"{url}/tasks", json={"title": "날짜 없음"})).status_code == 422
    assert (await client.post(f"{url}/tasks", json={"title": "x", "date": "2026-13-01"})).status_code == 422
    assert (await client.post(f"{url}/tasks", json={"title": " ", "date": "2026-11-30"})).status_code == 422


async def test_task_trash_and_restore(ctx):
    client, url = ctx["client"], ctx["url"]
    task = (await client.post(f"{url}/tasks", json={"title": "x", "date": "2026-11-30"})).json()
    assert (await client.delete(f"{url}/tasks/{task['id']}")).status_code == 204
    assert (await client.get(f"{url}/tasks")).json() == []
    assert [t["id"] for t in (await client.get(f"{url}/tasks", params={"status": "trash"})).json()] == [task["id"]]
    assert (await client.post(f"{url}/tasks/{task['id']}/restore")).json()["status"] == "active"


async def test_task_linked_to_section_shows_way_back(ctx):
    client, url = ctx["client"], ctx["url"]
    body = {"title": "견적 확인", "date": "2026-11-30", "source_doc_id": ctx["doc_id"], "source_section_id": "F06"}
    task = (await client.post(f"{url}/tasks", json=body)).json()
    assert task["source"] == {
        "doc_id": ctx["doc_id"], "doc_title": "팔찌 펀딩", "doc_status": "active",
        "section_id": "F06", "section_title": "제작·배송 계획",
    }
    assert (await client.post(f"{url}/tasks", json={**body, "source_section_id": "B01"})).status_code == 404
    await client.delete(f"{url}/docs/{ctx['doc_id']}")
    kept = (await client.get(f"{url}/tasks")).json()
    assert kept[0]["source"]["doc_status"] == "trash"


async def test_ai_proposes_tasks_without_registering_them(ctx, use_runtime, db):
    provider = FakeProvider([[tool_call("suggest_tasks", PROPOSAL), COMPLETED]])
    run_id = await propose(ctx, use_runtime(provider), request="견적 확인할 일")
    assert [t["name"] for t in provider.calls[0]["tools"]] == ["suggest_tasks"]
    reply = (await ctx["client"].get(f"{ctx['url']}/messages")).json()[-1]
    assert (reply["purpose"], reply["target_section_id"]) == ("task_proposal", "F06")
    proposals = reply["proposals"]
    assert [(p["title"], p["suggested_date"]) for p in proposals] == [
        ("제작처에 견적·가능 일정 확인", None), ("포장 샘플 받기", "2026-12-01"),
    ]
    assert all(p["proposal_id"] for p in proposals)
    assert await db.tasks.count_documents({}) == 0
    assert (await ctx["client"].get(f"{ctx['url']}/runs/{run_id}")).json()["status"] == "completed"


async def test_accepting_a_proposal_registers_once(ctx, use_runtime):
    await propose(ctx, use_runtime(FakeProvider([[tool_call("suggest_tasks", PROPOSAL), COMPLETED]])))
    proposal = (await ctx["client"].get(f"{ctx['url']}/messages")).json()[-1]["proposals"][0]
    body = {"title": "제작처에 견적 확인(수정)", "date": "2026-11-30", "source_doc_id": ctx["doc_id"],
            "source_section_id": "F06", "source_proposal_id": proposal["proposal_id"]}
    first = await ctx["client"].post(f"{ctx['url']}/tasks", json=body)
    again = await ctx["client"].post(f"{ctx['url']}/tasks", json={**body, "title": "또"})
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"] and again.json()["existing"] is True
    assert len((await ctx["client"].get(f"{ctx['url']}/tasks")).json()) == 1
    fake = await ctx["client"].post(f"{ctx['url']}/tasks", json={**body, "source_proposal_id": "0" * 24})
    assert fake.status_code == 404


@pytest.mark.parametrize("arguments", [
    {"tasks": [{"title": "x", "date": "내일", "note": ""}]},
    {"tasks": [{"title": "", "date": None, "note": ""}]},
    {"items": []},
])
async def test_invalid_proposals_fail_the_run(ctx, use_runtime, arguments):
    run_id = await propose(ctx, use_runtime(FakeProvider([[tool_call("suggest_tasks", arguments), COMPLETED]])))
    run = (await ctx["client"].get(f"{ctx['url']}/runs/{run_id}")).json()
    assert (run["status"], run["error_code"]) == ("failed", "INVALID_TOOL_ARGUMENTS")


async def test_answer_without_proposals_is_kept_as_text(ctx, use_runtime):
    await propose(ctx, use_runtime(FakeProvider([answer("지금 정보로는 제안할 할 일이 없어요")])))
    reply = (await ctx["client"].get(f"{ctx['url']}/messages")).json()[-1]
    assert reply["proposals"] == [] and reply["content"] == "지금 정보로는 제안할 할 일이 없어요"


async def test_demo_provider_offers_one_sample_proposal(ctx, use_runtime):
    await propose(ctx, use_runtime(DemoProvider()))
    reply = (await ctx["client"].get(f"{ctx['url']}/messages")).json()[-1]
    assert len(reply["proposals"]) == 1 and reply["proposals"][0]["suggested_date"] is None


async def test_permanent_project_delete_removes_tasks(ctx, db):
    client, url = ctx["client"], ctx["url"]
    await client.post(f"{url}/tasks", json={"title": "x", "date": "2026-11-30"})
    await client.delete(url)
    assert (await client.delete(url, params={"permanent": "true"})).status_code == 204
    assert await db.tasks.count_documents({}) == 0


async def test_null_fields_in_update_mean_no_change(ctx):
    client, url = ctx["client"], ctx["url"]
    task = (await client.post(f"{url}/tasks", json={"title": "재료 주문", "date": "2026-11-30"})).json()
    res = await client.patch(f"{url}/tasks/{task['id']}", json={"title": None, "date": None, "revision": task["revision"]})
    assert res.status_code == 200, res.text
    assert (res.json()["title"], res.json()["date"]) == ("재료 주문", "2026-11-30")
