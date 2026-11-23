"""AI 채팅 실행 흐름(services/ai_chat.py) 테스트."""
import asyncio
import json

import httpx
import pytest
from bson import ObjectId

from ai_fakes import COMPLETED, FakeProvider, answer, delta
from app.ai.events import RunEvents
from app.ai.provider import OpenAIProvider
from app.ai.responses import Delivery, ProviderError
from app.ai.runtime import Runtime
from app.services import ai_chat

MODEL = "gpt-6-luna"


@pytest.fixture
async def setup(client, login, db):
    me = await login(client)
    created = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return {"client": client, "user_id": ObjectId(me["id"]), "project_id": created["id"]}


def runtime_with(provider) -> Runtime:
    return Runtime(provider, RunEvents(), {}, MODEL)


async def run(db, setup, runtime, text="고객을 정리해 줘"):
    project = await db.projects.find_one({"_id": ObjectId(setup["project_id"])})
    run_id = await ai_chat.start_run(db, runtime, setup["user_id"], project, text)
    return run_id, runtime.tasks[str(run_id)]


async def kinds(runtime, run_id):
    return [event["kind"] async for event in runtime.events.subscribe(str(run_id))]


async def messages(db, setup):
    cursor = db.messages.find({"project_id": ObjectId(setup["project_id"])}).sort("ordinal", 1)
    return [(m["role"], m["content"], m["completion"]) async for m in cursor]


async def project_doc(db, setup):
    return await db.projects.find_one({"_id": ObjectId(setup["project_id"])})


async def test_answer_is_streamed_and_saved(db, setup):
    runtime = runtime_with(FakeProvider([answer("안녕", "하세요")]))
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(task, 5)
    assert await messages(db, setup) == [("USER", "고객을 정리해 줘", "COMPLETE"), ("ASSISTANT", "안녕하세요", "COMPLETE")]
    saved = await db.ai_runs.find_one({"_id": run_id})
    assistant = await db.messages.find_one({"role": "ASSISTANT"})
    assert (saved["status"], saved["assistant_message_id"]) == ("completed", assistant["_id"])
    seen = await kinds(runtime, run_id)
    assert seen[0] == "started" and "delta" in seen and seen[-1] == "completed"
    assert (await project_doc(db, setup))["active_run"] is None
    assert str(run_id) not in runtime.tasks


async def test_read_tool_returns_plan_and_reference_sections(db, setup):
    client, pid = setup["client"], setup["project_id"]
    rid = (await client.post("/projects", json={"name": "지난 팔찌"})).json()["id"]
    await client.put(f"/projects/{rid}/items/plan/ck_02", json={"content": "선물 고객", "revision": 1})
    await client.put(f"/projects/{pid}/items/plan/ck_01", json={"content": "칠보 팔찌", "revision": 1})
    await client.patch(f"/projects/{pid}", json={"chip_refs": [rid], "revision": 1})
    keys = ["ck_01", f"ref:{rid}:ck_02"]
    tool = {"type": "tool", "name": "read_project_sections", "call_id": "c1", "arguments": json.dumps({"keys": keys})}
    provider = FakeProvider([[tool, COMPLETED], answer("읽었어요")])
    runtime = runtime_with(provider)
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(task, 5)
    output = provider.calls[1]["input"][-1]
    assert output["type"] == "function_call_output" and output["call_id"] == "c1"
    contents = [section["content"] for section in json.loads(output["output"])]
    assert contents == ["칠보 팔찌", "선물 고객"]
    plan = await db.items.find_one({"project_id": ObjectId(pid), "key": "ck_01"})
    assert plan["revision"] == 2
    assert (await db.ai_runs.find_one({"_id": run_id}))["status"] == "completed"


@pytest.mark.parametrize(
    "call,code",
    [
        ({"type": "tool", "name": "delete_documents", "call_id": "c1", "arguments": "{}"}, "TOOL_NOT_ALLOWED"),
        ({"type": "tool", "name": "read_project_sections", "call_id": None, "arguments": "{}"}, "TOOL_NOT_ALLOWED"),
        ({"type": "tool", "name": "read_project_sections", "call_id": "c1", "arguments": "{\"keys\": 3}"},
         "INVALID_TOOL_ARGUMENTS"),
    ],
)
async def test_bad_tool_calls_fail(db, setup, call, code):
    runtime = runtime_with(FakeProvider([[call, COMPLETED]]))
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(task, 5)
    saved = await db.ai_runs.find_one({"_id": run_id})
    assert (saved["status"], saved["error_code"]) == ("failed", code)
    assert (await project_doc(db, setup))["active_run"] is None


async def test_tool_budget_is_six_rounds(db, setup):
    tool = {"type": "tool", "name": "read_project_sections", "call_id": "c1", "arguments": "{\"keys\": []}"}
    provider = FakeProvider([[tool, COMPLETED] for _ in range(7)])
    run_id, task = await run(db, setup, runtime_with(provider))
    await asyncio.wait_for(task, 5)
    assert len(provider.calls) == 6
    assert (await db.ai_runs.find_one({"_id": run_id}))["error_code"] == "TOOL_BUDGET_EXHAUSTED"


async def test_missing_key_fails_and_releases_lock(db, setup):
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    runtime = runtime_with(OpenAIProvider("", MODEL, transport))
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(task, 5)
    saved = await db.ai_runs.find_one({"_id": run_id})
    assert (saved["status"], saved["error_code"], saved["delivery"]) == ("failed", "KEY_MISSING_OR_INVALID", "not_dispatched")
    assert [m[0] for m in await messages(db, setup)] == ["USER"]
    assert (await project_doc(db, setup))["active_run"] is None
    assert (await kinds(runtime, run_id))[-1] == "failed"


async def test_stop_keeps_partial_answer(db, setup):
    gate = asyncio.Event()
    provider = FakeProvider([answer("반쯤", " 나머지")], gate=gate)
    runtime = runtime_with(provider)
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(provider.paused.wait(), 5)
    assert await ai_chat.stop_run(db, runtime, setup["user_id"], ObjectId(setup["project_id"]), str(run_id)) == "stop_requested"
    await asyncio.wait_for(task, 5)
    assert (await db.ai_runs.find_one({"_id": run_id}))["status"] == "stopped"
    assert (await messages(db, setup))[-1] == ("ASSISTANT", "반쯤", "PARTIAL")
    assert (await kinds(runtime, run_id))[-1] == "stopped"


async def test_stop_request_from_other_process(db, setup):
    gate = asyncio.Event()
    provider = FakeProvider([answer("반쯤", " 나머지")], gate=gate)
    runtime = runtime_with(provider)
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(provider.paused.wait(), 5)
    runtime.tasks.pop(str(run_id))
    assert await ai_chat.stop_run(db, runtime, setup["user_id"], ObjectId(setup["project_id"]), str(run_id)) == "stop_requested"
    assert (await db.ai_runs.find_one({"_id": run_id}))["stop_requested"] is True
    gate.set()
    await asyncio.wait_for(task, 5)
    assert (await db.ai_runs.find_one({"_id": run_id}))["status"] == "stopped"
    assert (await messages(db, setup))[-1][2] == "PARTIAL"


async def test_stop_finished_run_returns_its_status(db, setup):
    runtime = runtime_with(FakeProvider([answer("끝")]))
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(task, 5)
    assert await ai_chat.stop_run(db, runtime, setup["user_id"], ObjectId(setup["project_id"]), str(run_id)) == "completed"


async def test_unknown_outcome_marks_unknown(db, setup):
    runtime = runtime_with(FakeProvider([], error=ProviderError("TRANSPORT_ERROR", Delivery.UNKNOWN)))
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(task, 5)
    saved = await db.ai_runs.find_one({"_id": run_id})
    assert (saved["status"], saved["delivery"]) == ("unknown", "unknown")
    assert "outcome_unknown" in await kinds(runtime, run_id)
    assert (await project_doc(db, setup))["active_run"] is None


async def test_trashed_during_run_discards_answer(db, setup):
    gate = asyncio.Event()
    provider = FakeProvider([answer("늦은", " 답변")], gate=gate)
    run_id, task = await run(db, setup, runtime_with(provider))
    await asyncio.wait_for(provider.paused.wait(), 5)
    assert (await setup["client"].delete(f"/projects/{setup['project_id']}")).status_code == 204
    gate.set()
    await asyncio.wait_for(task, 5)
    saved = await db.ai_runs.find_one({"_id": run_id})
    assert (saved["status"], saved["error_code"]) == ("failed", "PROJECT_NOT_ACTIVE")
    assert [m[0] for m in await messages(db, setup)] == ["USER"]


async def test_compression_saves_capsule(db, setup):
    project = await project_doc(db, setup)
    from app.services import chat

    for role, text in [("USER", "a"), ("ASSISTANT", "b"), ("USER", "c"), ("ASSISTANT", "d")]:
        await chat.add_message(db, setup["user_id"], project["_id"], role, text)

    def count(wire):
        return 1_000 if "Earlier conversation summary" in json.dumps(wire, ensure_ascii=False) else 70_000

    runtime = runtime_with(FakeProvider([answer("압축 뒤 답변")], count=count))
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(task, 5)
    capsule = (await project_doc(db, setup))["chat_capsule"]
    assert capsule["revision"] == 1 and capsule["text"] == "요약" and len(capsule["covered_ids"]) == 2
    assert "compression_finished" in await kinds(runtime, run_id)
    assert (await db.ai_runs.find_one({"_id": run_id}))["status"] == "completed"


async def test_usage_saved_on_run(db, setup):
    usage = {"type": "usage", "input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    runtime = runtime_with(FakeProvider([[delta("답"), usage, COMPLETED]]))
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(task, 5)
    saved = await db.ai_runs.find_one({"_id": run_id})
    assert saved["usage"]["generation"]["total_tokens"] == 15
    assert saved["usage"]["generation"]["calls"] == 1


async def test_api_key_never_stored(db, setup):
    key = "sk-secret-xyz"

    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 20})
        body = (
            'data: {"type":"response.output_text.delta","delta":"키는 %s 입니다"}\n\n'
            'data: {"type":"response.completed","response":{"status":"completed","model":"%s"}}\n\n' % (key, MODEL)
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    runtime = runtime_with(OpenAIProvider(key, MODEL, httpx.MockTransport(handler)))
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(task, 5)
    stored = [m async for m in db.messages.find({})] + [r async for r in db.ai_runs.find({})]
    events = [e async for e in runtime.events.subscribe(str(run_id))]
    assert key not in repr(stored) and key not in repr(events)
    assert (await db.messages.find_one({"role": "ASSISTANT"}))["content"] == "키는 [REDACTED] 입니다"


async def test_double_stop_cancels_once(db, setup):
    gate = asyncio.Event()
    provider = FakeProvider([answer("반쯤", " 나머지")], gate=gate)
    runtime = runtime_with(provider)
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(provider.paused.wait(), 5)
    pid = ObjectId(setup["project_id"])
    await ai_chat.stop_run(db, runtime, setup["user_id"], pid, str(run_id))
    await ai_chat.stop_run(db, runtime, setup["user_id"], pid, str(run_id))
    assert task.cancelling() == 1
    await asyncio.wait_for(task, 5)
    assert (await project_doc(db, setup))["active_run"] is None
    assert (await db.ai_runs.find_one({"_id": run_id}))["status"] == "stopped"


async def test_cancel_during_final_save_does_not_save_twice(db, setup, monkeypatch):
    original = ai_chat.chat.add_message
    state = {"cancelled": False}

    async def cancelled_after_insert(db_, user_id, project_id, role, content, *args, **kwargs):
        message = await original(db_, user_id, project_id, role, content, *args, **kwargs)
        if role == "ASSISTANT" and not state["cancelled"]:
            state["cancelled"] = True
            raise asyncio.CancelledError
        return message

    monkeypatch.setattr(ai_chat.chat, "add_message", cancelled_after_insert)
    runtime = runtime_with(FakeProvider([answer("완성된 답변")]))
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(task, 5)
    assert [m[0] for m in await messages(db, setup)] == ["USER", "ASSISTANT"]
    assert (await project_doc(db, setup))["active_run"] is None


async def test_unexpected_error_is_logged(db, setup, monkeypatch, caplog):
    async def broken(*args, **kwargs):
        raise RuntimeError("boom-detail")

    monkeypatch.setattr(ai_chat.ai_context, "build_sections", broken)
    runtime = runtime_with(FakeProvider([answer("x")]))
    with caplog.at_level("ERROR"):
        run_id, task = await run(db, setup, runtime)
        await asyncio.wait_for(task, 5)
    assert (await db.ai_runs.find_one({"_id": run_id}))["error_code"] == "INTERNAL_ERROR"
    assert "boom-detail" in caplog.text


async def test_heartbeat_survives_a_failed_write(db, setup, monkeypatch):
    monkeypatch.setattr(ai_chat.ai_runs, "HEARTBEAT_SECONDS", 0.01)
    calls = []

    async def flaky(db_, run_id):
        calls.append(run_id)
        if len(calls) == 1:
            raise RuntimeError("atlas blip")

    monkeypatch.setattr(ai_chat.ai_runs, "heartbeat", flaky)
    gate = asyncio.Event()
    provider = FakeProvider([answer("반쯤", " 나머지")], gate=gate)
    runtime = runtime_with(provider)
    run_id, task = await run(db, setup, runtime)
    await asyncio.wait_for(provider.paused.wait(), 5)
    for _ in range(100):
        if len(calls) >= 3:
            break
        await asyncio.sleep(0.01)
    gate.set()
    await asyncio.wait_for(task, 5)
    assert len(calls) >= 3


async def test_halt_mid_stream_closes_provider_stream(db, setup, monkeypatch):
    monkeypatch.setattr(ai_chat, "CHECK_INTERVAL_SECONDS", 0)
    gate = asyncio.Event()
    usage = {"type": "usage", "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
    provider = FakeProvider([[delta("늦은"), delta(" 답변"), usage, COMPLETED]], gate=gate)
    run_id, task = await run(db, setup, runtime_with(provider))
    await asyncio.wait_for(provider.paused.wait(), 5)
    assert (await setup["client"].delete(f"/projects/{setup['project_id']}")).status_code == 204
    gate.set()
    await asyncio.wait_for(task, 5)
    saved = await db.ai_runs.find_one({"_id": run_id})
    assert (saved["status"], saved["error_code"]) == ("failed", "PROJECT_NOT_ACTIVE")
    assert saved["usage"]["generation"]["pending"] == 0
