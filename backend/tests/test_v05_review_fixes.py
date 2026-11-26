"""v05 최종 리뷰 지적의 회귀 테스트."""
import asyncio
import json

import pytest

from ai_fakes import COMPLETED, FakeProvider, answer, tool_call
from app.ai.context import build_wire
from app.ai.events import RunEvents
from app.ai.runtime import Runtime, set_runtime

ONE = {"tasks": [{"title": "견적 확인", "date": None, "note": ""}]}


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
    doc = (await client.post(f"{url}/docs", json={"template_id": "funding"})).json()
    return {"client": client, "url": url, "doc_id": doc["id"], "base": f"{url}/docs/{doc['id']}"}


async def run_and_wait(ctx, runtime, path, body):
    res = await ctx["client"].post(path, json=body)
    assert res.status_code == 202, res.text
    await asyncio.wait_for(runtime.tasks[res.json()["run_id"]], 5)


async def proposal(ctx, use_runtime, arguments=ONE):
    runtime = use_runtime(FakeProvider([[tool_call("suggest_tasks", arguments), COMPLETED]]))
    await run_and_wait(ctx, runtime, f"{ctx['base']}/sections/F06/task-proposals", {})
    return (await ctx["client"].get(f"{ctx['base']}/sections/F06/messages")).json()[-1]


# 1. 제안에서 만든 일정은 제안의 문서·항목을 쓴다
async def test_task_from_proposal_takes_doc_and_section_from_proposal(ctx, use_runtime):
    reply = await proposal(ctx, use_runtime)
    pid = reply["proposals"][0]["proposal_id"]
    task = (await ctx["client"].post(f"{ctx['url']}/tasks", json={"title": "견적", "date": "2026-11-30", "source_proposal_id": pid})).json()
    assert (task["source"]["doc_id"], task["source"]["section_id"]) == (ctx["doc_id"], "F06")


async def test_task_from_proposal_rejects_a_different_section(ctx, use_runtime):
    reply = await proposal(ctx, use_runtime)
    body = {"title": "견적", "date": "2026-11-30", "source_proposal_id": reply["proposals"][0]["proposal_id"],
            "source_doc_id": ctx["doc_id"], "source_section_id": "F01"}
    res = await ctx["client"].post(f"{ctx['url']}/tasks", json=body)
    assert res.status_code == 422 and res.json()["code"] == "PROPOSAL_SOURCE_MISMATCH"


# 2. 중복 방지 인덱스는 서버가 시작할 때도 만든다
async def test_startup_creates_uniqueness_indexes(db):
    import main

    await db.tasks.drop_indexes()
    await main.ensure_indexes()
    names = [index["key"] async for index in await db.tasks.list_indexes()]
    assert any("source_proposal_id" in dict(key) for key in names)


# 3. 재방문하면 이미 등록한 제안을 알 수 있다
async def test_registered_proposals_show_their_task(ctx, use_runtime):
    reply = await proposal(ctx, use_runtime)
    pid = reply["proposals"][0]["proposal_id"]
    task = (await ctx["client"].post(f"{ctx['url']}/tasks", json={"title": "견적", "date": "2026-11-30", "source_proposal_id": pid})).json()
    for path in (f"{ctx['base']}/sections/F06/messages", f"{ctx['url']}/messages"):
        listed = (await ctx["client"].get(path)).json()[-1]["proposals"][0]
        assert (listed["task_id"], listed["task_status"]) == (task["id"], "active")
    await ctx["client"].delete(f"{ctx['url']}/tasks/{task['id']}")
    listed = (await ctx["client"].get(f"{ctx['base']}/sections/F06/messages")).json()[-1]["proposals"][0]
    assert listed["task_status"] == "trash"


# 4. 도구가 없으면 도구 관련 필드를 보내지 않는다
def test_wire_without_tools_has_no_tool_fields():
    wire = build_wire([], [], "x", [], "m")
    assert "tools" not in wire and "parallel_tool_calls" not in wire
    with_tools = build_wire([], [], "x", [{"type": "function", "name": "t"}], "m")
    assert with_tools["parallel_tool_calls"] is False and with_tools["tools"]


# 5. 첨부를 읽지 않으면 AI에게 그 사실을 알린다
async def test_unread_attachments_are_mentioned(ctx, use_runtime, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "ai_read_attachments", False)
    project_id = ctx["url"].rsplit("/", 1)[-1]
    asset = (await ctx["client"].post("/assets", data={"project_id": project_id, "asset_type": "document"},
                                      files={"file": ("견적.txt", "비밀 견적".encode(), "text/plain")})).json()
    provider = FakeProvider([answer("답")])
    runtime = use_runtime(provider)
    await run_and_wait(ctx, runtime, f"{ctx['url']}/runs", {"text": "파일 봐 줘", "asset_ids": [asset["id"]]})
    wire = json.dumps(provider.calls[0]["input"], ensure_ascii=False)
    assert "비밀 견적" not in wire and "attachments_not_read" in wire and "견적.txt" in wire


# 6. 할 일 후보가 없거나 많아도 실행은 끝난다
async def test_empty_proposal_list_is_not_an_error(ctx, use_runtime):
    reply = await proposal(ctx, use_runtime, {"tasks": []})
    assert reply["proposals"] == [] and reply["content"]


async def test_more_than_five_proposals_are_cut(ctx, use_runtime):
    many = {"tasks": [{"title": f"일 {n}", "date": None, "note": ""} for n in range(7)]}
    reply = await proposal(ctx, use_runtime, many)
    assert len(reply["proposals"]) == 5


# 9. 후보를 적용하면 마지막 항목이 바뀐다
async def test_apply_updates_last_section(ctx, use_runtime):
    runtime = use_runtime(FakeProvider([answer("후보")]))
    await run_and_wait(ctx, runtime, f"{ctx['base']}/sections/F02/assist", {"action": "draft"})
    reply = (await ctx["client"].get(f"{ctx['base']}/sections/F02/messages")).json()[-1]
    await ctx["client"].put(f"{ctx['base']}/sections/F07", json={"text": "x", "revision": 0})
    await ctx["client"].post(f"{ctx['base']}/sections/F02/apply", json={"message_id": reply["id"], "draft_revision": 0})
    assert (await ctx["client"].get(ctx["base"])).json()["last_section_id"] == "F02"


# 계정 전체 일정
async def test_account_wide_task_list(ctx, client, login):
    other = (await client.post("/projects", json={"name": "다른 상품"})).json()
    await client.post(f"{ctx['url']}/tasks", json={"title": "A", "date": "2026-12-05"})
    await client.post(f"/projects/{other['id']}/tasks", json={"title": "B", "date": "2026-11-28"})
    await client.post(f"/projects/{other['id']}/tasks", json={"title": "C", "date": "2027-01-10"})
    listed = (await client.get("/tasks", params={"from": "2026-11-01", "to": "2026-12-31"})).json()
    assert [(t["title"], t["project_name"]) for t in listed] == [("B", "다른 상품"), ("A", "칠보 팔찌")]
    await login(client, email="b@test.com")
    assert (await client.get("/tasks")).json() == []
