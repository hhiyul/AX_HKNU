import json

import pytest

from app.ai.p5.usage import UsageTracker, valid_usage


@pytest.mark.asyncio
async def test_per_run_usage_tracks_retries_missing_and_not_dispatched():
    events = []
    async def emit(kind, text): events.append((kind, json.loads(text)))
    tracker = UsageTracker(emit, "openai")
    await tracker.start("compression")
    await tracker.finish("compression", {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110})
    await tracker.start("compression")
    await tracker.finish("compression", not_dispatched=True)
    await tracker.start("generation")
    await tracker.finish("generation")
    snapshot = events[-1][1]["parts"]
    assert snapshot["compression"] == {"calls": 2, "reported": 1, "missing": 0, "pending": 0,
        "not_dispatched": 1, "input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    assert snapshot["generation"]["missing"] == 1 and snapshot["generation"]["reported"] == 0
    assert valid_usage({"input_tokens": True, "output_tokens": 0, "total_tokens": 1}) is None
    assert valid_usage({"input_tokens": 2, "output_tokens": 1, "total_tokens": 99}) is None
