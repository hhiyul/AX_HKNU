"""AI 채팅 실행 API(/projects/{id}/runs) 테스트."""
import asyncio
import json
import time

import pytest

from ai_fakes import FakeProvider, answer
from app.ai.events import RunEvents
from app.ai.runtime import Runtime, set_runtime


@pytest.fixture
def use_runtime():
    def install(provider, keep_seconds: float = 600) -> Runtime:
        runtime = Runtime(provider, RunEvents(keep_seconds), {}, "gpt-6-luna")
        set_runtime(runtime)
        return runtime

    yield install
    set_runtime(None)


@pytest.fixture
async def url(client, login):
    await login(client)
    project = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return f"/projects/{project['id']}"


async def start(client, url, text="고객을 정리해 줘"):
    res = await client.post(f"{url}/runs", json={"text": text})
    assert res.status_code == 202, res.text
    return res.json()["run_id"]


async def finish(runtime, run_id):
    task = runtime.tasks.get(run_id)
    if task is not None:
        await asyncio.wait_for(task, 5)


def stream_events(body: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]


async def test_run_round_trip(client, url, use_runtime):
    runtime = use_runtime(FakeProvider([answer("안녕하세요")]))
    run_id = await start(client, url)
    await finish(runtime, run_id)
    run = (await client.get(f"{url}/runs/{run_id}")).json()
    assert (run["id"], run["status"], run["error_code"]) == (run_id, "completed", None)
    assert run["usage"]["generation"]["calls"] == 1
    messages = (await client.get(f"{url}/messages")).json()
    assert [(m["role"], m["content"]) for m in messages] == [("USER", "고객을 정리해 줘"), ("ASSISTANT", "안녕하세요")]
    assert run["user_message_id"] == messages[0]["id"] and run["assistant_message_id"] == messages[1]["id"]


async def test_second_run_conflicts_until_first_finishes(client, url, use_runtime):
    gate = asyncio.Event()
    provider = FakeProvider([answer("첫", " 답변"), answer("두 번째")], gate=gate)
    runtime = use_runtime(provider)
    run_id = await start(client, url)
    await asyncio.wait_for(provider.paused.wait(), 5)
    res = await client.post(f"{url}/runs", json={"text": "또"})
    assert res.status_code == 409 and res.json()["code"] == "RUN_ACTIVE"
    gate.set()
    await finish(runtime, run_id)
    await finish(runtime, await start(client, url, "또"))


async def test_stream_returns_events_in_order(client, url, use_runtime):
    runtime = use_runtime(FakeProvider([answer("안녕")]))
    run_id = await start(client, url)
    await finish(runtime, run_id)
    res = await client.get(f"{url}/runs/{run_id}/stream")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    events = stream_events(res.text)
    kinds = [event["kind"] for event in events]
    assert kinds[0] == "started" and kinds[-1] == "completed"
    assert kinds.index("delta") < kinds.index("completed")
    assert f"id: {events[0]['seq']}" in res.text
    later = stream_events((await client.get(f"{url}/runs/{run_id}/stream", params={"after": 1})).text)
    assert [event["seq"] for event in later] == [event["seq"] for event in events[1:]]


async def test_stream_of_finished_or_expired_run_ends(client, url, use_runtime):
    runtime = use_runtime(FakeProvider([answer("안녕")]), keep_seconds=0)
    run_id = await start(client, url)
    await finish(runtime, run_id)
    runtime.events.publish("other-run", "started", "")
    began = time.monotonic()
    res = await client.get(f"{url}/runs/{run_id}/stream")
    assert res.status_code == 200 and stream_events(res.text) == []
    assert time.monotonic() - began < 2


async def test_run_endpoints_check_owner(client, url, login, use_runtime):
    runtime = use_runtime(FakeProvider([answer("안녕")]))
    run_id = await start(client, url)
    await finish(runtime, run_id)
    await login(client, email="b@test.com")
    assert (await client.post(f"{url}/runs", json={"text": "x"})).status_code == 404
    assert (await client.get(f"{url}/runs/{run_id}")).status_code == 404
    assert (await client.get(f"{url}/runs/{run_id}/stream")).status_code == 404
    assert (await client.post(f"{url}/runs/{run_id}/stop")).status_code == 404


async def test_unknown_run_is_404(client, url, use_runtime):
    use_runtime(FakeProvider([]))
    assert (await client.get(f"{url}/runs/not-an-id")).status_code == 404
    assert (await client.get(f"{url}/runs/{'0' * 24}/stream")).status_code == 404


async def test_trashed_project_can_still_be_stopped(client, url, use_runtime):
    gate = asyncio.Event()
    provider = FakeProvider([answer("반쯤", " 나머지")], gate=gate)
    runtime = use_runtime(provider)
    run_id = await start(client, url)
    await asyncio.wait_for(provider.paused.wait(), 5)
    assert (await client.delete(url)).status_code == 204
    assert (await client.get(f"{url}/runs/{run_id}")).json()["status"] == "running"
    res = await client.post(f"{url}/runs/{run_id}/stop")
    assert res.status_code == 200 and res.json() == {"status": "stop_requested"}
    await finish(runtime, run_id)
    assert (await client.get(f"{url}/runs/{run_id}")).json()["status"] in ("stopped", "failed")
    res = await client.post(f"{url}/runs", json={"text": "또"})
    assert res.status_code == 409 and res.json()["code"] == "PROJECT_NOT_ACTIVE"


async def test_empty_text_rejected(client, url, use_runtime):
    use_runtime(FakeProvider([]))
    res = await client.post(f"{url}/runs", json={"text": ""})
    assert res.status_code == 422
    assert (await client.post(f"{url}/runs", json={"text": "x", "mode": "document"})).status_code == 422


async def test_stream_headers_and_last_event_id(client, url, use_runtime):
    runtime = use_runtime(FakeProvider([answer("안녕")]))
    run_id = await start(client, url)
    await finish(runtime, run_id)
    res = await client.get(f"{url}/runs/{run_id}/stream", headers={"Last-Event-ID": "1"})
    assert res.headers["x-accel-buffering"] == "no"
    assert stream_events(res.text)[0]["seq"] == 2


async def test_startup_reap_survives_unreachable_db(monkeypatch, caplog):
    import main

    def unreachable():
        raise RuntimeError("no atlas")

    monkeypatch.setattr(main, "get_db", unreachable)
    with caplog.at_level("ERROR"):
        await main.reap_stale_runs()
    assert "no atlas" in caplog.text
