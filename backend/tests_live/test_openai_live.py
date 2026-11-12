"""Opt-in, billable smoke test. Never enabled by the presence of a key alone."""

import os

import pytest

from app.ai.responses import GenerationRequest, ProviderConfig, ResponsesClient


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("AX_OPENAI_LIVE_TEST") != "1", reason="explicit live-test opt-in required")
async def test_responses_round_trip_once():
    config = ProviderConfig.from_environment()
    result = await ResponsesClient(config).generate(
        GenerationRequest("Please reply with a short test message.", max_output_tokens=128)
    )
    assert result.model == config.model
    assert result.response_id
    assert result.text.strip()
