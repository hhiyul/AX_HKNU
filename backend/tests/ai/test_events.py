"""실행별 메모리 사건 대기열(app/ai/events.py)과 런타임 테스트."""
import asyncio

from app.ai.events import RunEvents
from app.ai.provider import DemoProvider
from app.ai.runtime import Runtime, get_runtime, set_runtime


async def collect(events: RunEvents, run_id: str, after: int = 0) -> list[dict]:
    return [event async for event in events.subscribe(run_id, after)]


async def test_subscribe_replays_after_seq_and_ends_on_close():
    events = RunEvents()
    for kind in ("started", "delta", "delta"):
        events.publish("r1", kind, kind)
    reader = asyncio.create_task(collect(events, "r1", after=1))
    await asyncio.sleep(0.05)
    assert events.publish("r1", "completed", "완료") == 4
    events.close("r1")
    received = await asyncio.wait_for(reader, 3)
    assert [event["seq"] for event in received] == [2, 3, 4]
    assert received[-1] == {"seq": 4, "kind": "completed", "content": "완료"}


async def test_unknown_run_ends_immediately():
    assert await asyncio.wait_for(collect(RunEvents(), "nope"), 1) == []


async def test_closed_runs_expire():
    events = RunEvents(keep_seconds=0)
    events.publish("old", "started", "")
    events.close("old")
    events.publish("new", "started", "")
    assert await asyncio.wait_for(collect(events, "old"), 1) == []


async def test_closed_run_replays_within_keep_window():
    events = RunEvents()
    events.publish("r1", "started", "")
    events.close("r1")
    assert [event["kind"] for event in await asyncio.wait_for(collect(events, "r1"), 1)] == ["started"]


def test_runtime_is_a_replaceable_singleton():
    custom = Runtime(DemoProvider(), RunEvents(), {}, "m-test")
    set_runtime(custom)
    try:
        assert get_runtime() is custom
    finally:
        set_runtime(None)
    default = get_runtime()
    assert isinstance(default.provider, DemoProvider) and default.model == "gpt-6-luna"
    assert get_runtime() is default
    set_runtime(None)


async def test_subscribe_yields_keepalive_while_waiting():
    events = RunEvents()
    events.publish("r1", "started", "")
    stream = events.subscribe("r1", ping_seconds=0.05)
    assert (await anext(stream))["kind"] == "started"
    assert await asyncio.wait_for(anext(stream), 1) is None
    await stream.aclose()
