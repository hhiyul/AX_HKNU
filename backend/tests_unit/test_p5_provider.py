import json
import httpx
import pytest

from app.ai.p5.provider import OpenAIProvider
from app.ai.responses import ProviderError


@pytest.mark.asyncio
async def test_count_and_stream_use_same_input_and_terminal_event():
    requests = []
    async def handler(request):
        requests.append(request)
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 20})
        data = 'data: {"type":"response.output_text.delta","delta":"안녕"}\n\ndata: {"type":"response.completed","response":{"status":"completed","model":"gpt-6-luna"}}\n\n'
        return httpx.Response(200, text=data, headers={"content-type": "text/event-stream"})
    client = OpenAIProvider("sk-fake-test", httpx.MockTransport(handler))
    wire = {"model": "gpt-6-luna", "input": [{"role": "user", "content": "hello"}], "tools": []}
    assert await client.count(wire) == 20
    events = [e async for e in client.stream(wire)]
    assert events == [{"type": "delta", "text": "안녕"}, {"type": "completed"}]
    counted, generated = [json.loads(r.content) for r in requests]
    assert all(generated[k] == counted[k] for k in wire)
    assert generated["stream"] is True and generated["store"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("usage,expected", [
    ({"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}, True),
    (None, False),
    ({"input_tokens": True, "output_tokens": 30, "total_tokens": 31}, False),
    ({"input_tokens": 120, "output_tokens": 30, "total_tokens": 999}, False),
])
async def test_stream_usage_is_reported_only_when_available_and_valid(usage, expected):
    async def handler(request):
        event = {"type": "response.completed", "response": {"status": "completed", "model": "gpt-6-luna", "usage": usage}}
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n", headers={"content-type": "text/event-stream"})
    client = OpenAIProvider("sk-fake-test", httpx.MockTransport(handler))
    events = [e async for e in client.stream({"model": "gpt-6-luna", "input": [], "tools": []})]
    assert events[-1] == {"type": "completed"}
    assert (len(events) == 2) is expected
    if expected:
        assert events[0] == {"type": "usage", **usage}


@pytest.mark.asyncio
async def test_summary_and_search_return_usage_without_injecting_it_into_text_or_tool_result():
    async def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 20})
        payload = {"id": "resp-test", "status": "completed", "model": "gpt-6-luna", "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "result"}]}
        ], "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}}
        return httpx.Response(200, json=payload)
    client = OpenAIProvider("sk-fake-test", httpx.MockTransport(handler))
    summary = await client.summarize([{"role": "user", "content": "old"}])
    assert summary == "result" and summary.usage["total_tokens"] == 25
    search = await client.web_search("public")
    assert search.usage["total_tokens"] == 25
    assert json.loads(json.dumps(search)) == {"text": "result", "urls": []}


@pytest.mark.asyncio
async def test_failed_response_keeps_reported_usage_without_claiming_success():
    usage = {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}
    async def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 20})
        if json.loads(request.content).get("stream"):
            event = {"type": "response.incomplete", "response": {"usage": usage}}
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")
        return httpx.Response(200, json={"status": "incomplete", "usage": usage})
    client = OpenAIProvider("sk-fake-test", httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as failure:
        await client.summarize([{"role": "user", "content": "old"}])
    assert failure.value.usage == usage
    seen = []
    with pytest.raises(ProviderError):
        async for event in client.stream({"model": "gpt-6-luna", "input": [], "tools": []}): seen.append(event)
    assert seen == [{"type": "usage", **usage}]
