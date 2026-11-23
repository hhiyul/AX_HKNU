"""AI 채팅 2단계: 웹 검색 테스트."""
import asyncio
import json

import pytest
from bson import ObjectId

from ai_fakes import COMPLETED, FakeProvider, answer, tool_call
from app.ai.events import RunEvents
from app.ai.responses import Delivery, ProviderError
from app.ai.runtime import Runtime
from app.ai.search_policy import GENERAL_CONCEPTS, generalized_query
from app.services import ai_chat


@pytest.fixture
async def setup(client, login, db):
    me = await login(client)
    created = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return {"client": client, "user_id": ObjectId(me["id"]), "project_id": created["id"]}


async def enable_search(setup, enabled=True):
    url = f"/projects/{setup['project_id']}"
    revision = (await setup["client"].get(url)).json()["revision"]
    res = await setup["client"].patch(url, json={"search_enabled": enabled, "revision": revision})
    assert res.status_code == 200, res.text


async def run(db, setup, provider, text="시장 사례 찾아 줘", **options):
    runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
    project = await db.projects.find_one({"_id": ObjectId(setup["project_id"])})
    run_id = await ai_chat.start_run(db, runtime, setup["user_id"], project, text, **options)
    return runtime, run_id, runtime.tasks[str(run_id)]


def tool_names(wire):
    return [tool["name"] for tool in wire["tools"]]


async def kinds_and_contents(runtime, run_id):
    return [(e["kind"], e["content"]) async for e in runtime.events.subscribe(str(run_id))]


def test_generalized_query_accepts_only_fixed_concepts():
    assert generalized_query(["craft", "price"]) == "수공예품 가격 비교"
    for bad in [[], ["craft"] * 2, ["내 브랜드"], ["a", "b", "c", "d", "e"], "craft", [1]]:
        with pytest.raises(ValueError):
            generalized_query(bad)
    assert "craft" in GENERAL_CONCEPTS


async def test_search_disabled_offers_no_search_tool(db, setup):
    provider = FakeProvider([answer("답")])
    runtime, run_id, task = await run(db, setup, provider, public_search_terms="칠보 가격")
    await asyncio.wait_for(task, 5)
    names = tool_names(provider.calls[0])
    assert "web_search" not in names and "web_search_generalized" not in names


async def test_generalized_search_runs_and_feeds_result(db, setup):
    await enable_search(setup)
    result = {"text": "수공예 팔찌는 3~5만원대가 많다", "urls": ["https://example.com/a"]}
    provider = FakeProvider(
        [[tool_call("web_search_generalized", {"concepts": ["craft", "price"]}), COMPLETED], answer("정리했어요")],
        search_results=[result],
    )
    runtime, run_id, task = await run(db, setup, provider)
    await asyncio.wait_for(task, 5)
    assert provider.queries == ["수공예품 가격 비교"]
    assert "web_search_generalized" in tool_names(provider.calls[0])
    assert "web_search" not in tool_names(provider.calls[0])
    output = json.loads(provider.calls[1]["input"][-1]["output"])
    assert output["text"] == result["text"] and output["urls"] == result["urls"]
    events = await kinds_and_contents(runtime, run_id)
    assert ("tool_query", "일반화된 검색어: 수공예품 가격 비교") in events
    assert any(kind == "tool_result" and "https://example.com/a" in content for kind, content in events)
    saved = await db.ai_runs.find_one({"_id": run_id})
    assert saved["status"] == "completed" and saved["usage"]["web_search"]["calls"] == 1


async def test_explicit_search_uses_only_allowed_words(db, setup):
    await enable_search(setup)
    provider = FakeProvider([[tool_call("web_search", {"query": "칠보 가격"}), COMPLETED], answer("답")])
    runtime, run_id, task = await run(db, setup, provider, public_search_terms="칠보 팔찌 가격")
    await asyncio.wait_for(task, 5)
    assert provider.queries == ["칠보 가격"]
    assert "web_search" in tool_names(provider.calls[0])
    prefix = provider.calls[0]["input"][1]["content"]
    assert "칠보 팔찌 가격" in prefix


async def test_explicit_search_with_other_words_is_refused_and_retried(db, setup):
    # 한국어 조사("팔찌의")처럼 허용 단어와 다르면 검색하지 않고 AI에게 다시 고르게 한다.
    await enable_search(setup)
    provider = FakeProvider([
        [tool_call("web_search", {"query": "팔찌의 가격"}), COMPLETED],
        [tool_call("web_search", {"query": "팔찌 가격"}, call_id="c2"), COMPLETED],
        answer("답"),
    ])
    runtime, run_id, task = await run(db, setup, provider, public_search_terms="칠보 팔찌 가격")
    await asyncio.wait_for(task, 5)
    refused = json.loads(provider.calls[1]["input"][-1]["output"])
    assert refused["error"] == "SEARCH_NOT_ALLOWED" and "칠보 팔찌 가격" in refused["message"]
    assert provider.queries == ["팔찌 가격"]
    assert (await db.ai_runs.find_one({"_id": run_id}))["status"] == "completed"


async def test_explicit_search_tool_not_offered_without_terms(db, setup):
    await enable_search(setup)
    provider = FakeProvider([[tool_call("web_search", {"query": "칠보"}), COMPLETED]])
    runtime, run_id, task = await run(db, setup, provider)
    await asyncio.wait_for(task, 5)
    assert "web_search" not in tool_names(provider.calls[0])
    assert (await db.ai_runs.find_one({"_id": run_id}))["error_code"] == "TOOL_NOT_ALLOWED"


async def test_generalized_search_rejects_free_text(db, setup):
    await enable_search(setup)
    provider = FakeProvider(
        [[tool_call("web_search_generalized", {"concepts": ["칠보공방 김씨"]}), COMPLETED], answer("검색 없이 답")]
    )
    runtime, run_id, task = await run(db, setup, provider)
    await asyncio.wait_for(task, 5)
    assert provider.queries == []
    assert json.loads(provider.calls[1]["input"][-1]["output"])["error"] == "SEARCH_NOT_ALLOWED"
    assert (await db.ai_runs.find_one({"_id": run_id}))["status"] == "completed"


async def test_search_turned_off_midrun_drops_result(db, setup):
    await enable_search(setup)
    gate = asyncio.Event()
    provider = FakeProvider(
        [[tool_call("web_search_generalized", {"concepts": ["market"]}), COMPLETED], answer("검색 없이 답")],
        search_gate=gate,
    )
    runtime, run_id, task = await run(db, setup, provider)
    await asyncio.wait_for(provider.searching.wait(), 5)
    await enable_search(setup, False)
    gate.set()
    await asyncio.wait_for(task, 5)
    output = json.loads(provider.calls[1]["input"][-1]["output"])
    assert output == {"error": "SEARCH_DISABLED"}
    assert "web_search_generalized" not in tool_names(provider.calls[1])
    assert (await db.ai_runs.find_one({"_id": run_id}))["status"] == "completed"


async def test_failed_search_is_reported_not_fabricated(db, setup):
    await enable_search(setup)
    provider = FakeProvider(
        [[tool_call("web_search_generalized", {"concepts": ["market"]}), COMPLETED], answer("검색 실패를 알림")],
        search_results=[ProviderError("SEARCH_NOT_PERFORMED", Delivery.RESPONDED)],
    )
    runtime, run_id, task = await run(db, setup, provider)
    await asyncio.wait_for(task, 5)
    output = json.loads(provider.calls[1]["input"][-1]["output"])
    assert output["error"] == "SEARCH_FAILED"
    assert "web_search_generalized" not in tool_names(provider.calls[1])
    assert (await db.ai_runs.find_one({"_id": run_id}))["status"] == "completed"


async def test_unknown_search_outcome_marks_run_unknown(db, setup):
    await enable_search(setup)
    provider = FakeProvider(
        [[tool_call("web_search_generalized", {"concepts": ["market"]}), COMPLETED]],
        search_results=[ProviderError("TRANSPORT_ERROR", Delivery.UNKNOWN)],
    )
    runtime, run_id, task = await run(db, setup, provider)
    await asyncio.wait_for(task, 5)
    saved = await db.ai_runs.find_one({"_id": run_id})
    assert (saved["status"], saved["delivery"]) == ("unknown", "unknown")


async def test_runs_api_accepts_search_terms(client, setup):
    from app.ai.runtime import set_runtime

    provider = FakeProvider([answer("답")])
    runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
    set_runtime(runtime)
    try:
        url = f"/projects/{setup['project_id']}/runs"
        res = await client.post(url, json={"text": "찾아 줘", "public_search_terms": "칠보 가격"})
        assert res.status_code == 202, res.text
        await asyncio.wait_for(runtime.tasks[res.json()["run_id"]], 5)
        assert (await client.post(url, json={"text": "x", "public_search_terms": "가" * 501})).status_code == 422
    finally:
        set_runtime(None)
