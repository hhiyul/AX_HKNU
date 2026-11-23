"""BAZE(factchat) API 게이트웨이 연결: https://api.baze.co/v1/gateway 의 Responses API."""
import json

import httpx
import pytest

from app.ai.provider import OpenAIProvider, make_provider
from app.ai.responses import ProviderError
from app.config import Settings

GATEWAY = "https://api.baze.co/v1/gateway"
DONE = 'data: {"type":"response.output_text.delta","delta":"안녕"}\n\ndata: {"type":"response.completed","response":{"status":"completed","model":"%s"}}\n\n'


def gateway(handler, effort=None, model="gpt-6-luna"):
    return OpenAIProvider("sk-gw-test", model, httpx.MockTransport(handler), base_url=GATEWAY, reasoning_effort=effort)


def test_settings_build_a_gateway_provider():
    provider = make_provider(Settings(ai_provider="openai", openai_api_key="k", openai_model="gpt-6-luna",
                                      openai_base_url=GATEWAY + "/"))
    assert provider.responses_url == "https://api.baze.co/v1/gateway/responses/"
    assert provider.supports_web_search is False
    official = make_provider(Settings(ai_provider="openai", openai_api_key="k"))
    assert official.responses_url == "https://api.openai.com/v1/responses" and official.supports_web_search is True


async def test_gateway_stream_sends_only_documented_fields():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, text=DONE % "gpt-6-luna", headers={"content-type": "text/event-stream"})

    wire = {"model": "gpt-6-luna", "input": [{"role": "user", "content": "hi"}]}
    events = [e async for e in gateway(handler).stream(wire)]
    assert events == [{"type": "delta", "text": "안녕"}, {"type": "completed"}]
    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.baze.co/v1/gateway/responses/"
    assert request.headers["authorization"] == "Bearer sk-gw-test"
    assert body["reasoning"] == {"effort": "low"} and body["stream"] is True
    assert "truncation" not in body and "store" not in body


async def test_gateway_reasoning_effort_is_configurable():
    seen = []

    async def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=DONE % "gpt-6-luna", headers={"content-type": "text/event-stream"})

    [e async for e in gateway(handler, effort="medium").stream({"model": "gpt-6-luna", "input": []})]
    assert seen[0]["reasoning"] == {"effort": "medium"}


async def test_gateway_counts_tokens_locally():
    def handler(request):
        raise AssertionError("게이트웨이에는 토큰 세기 주소가 없어 부르면 안 된다")

    n = await gateway(handler).count({"model": "gpt-6-luna", "input": [{"role": "user", "content": "가" * 1000}]})
    assert 500 <= n <= 2000


async def test_gateway_accepts_the_model_name_it_reports():
    async def handler(request):
        return httpx.Response(200, text=DONE % "openai/gpt-6-luna-gw", headers={"content-type": "text/event-stream"})

    events = [e async for e in gateway(handler).stream({"model": "gpt-6-luna", "input": []})]
    assert events[-1] == {"type": "completed"}


@pytest.mark.parametrize("status,code", [(401, "KEY_MISSING_OR_INVALID"), (402, "CREDIT_EXHAUSTED"),
                                         (403, "ACCESS_DENIED"), (429, "RATE_LIMITED"), (500, "PROVIDER_ERROR")])
async def test_gateway_errors_have_their_own_codes(status, code):
    provider = gateway(lambda request: httpx.Response(status, json={"detail": {"code": status, "message": "x"}}))
    with pytest.raises(ProviderError) as caught:
        [e async for e in provider.stream({"model": "gpt-6-luna", "input": []})]
    assert caught.value.code == code
    with pytest.raises(ProviderError) as caught:
        await provider.summarize([{"role": "user", "content": "a"}])
    assert caught.value.code == code


async def test_gateway_summary_uses_gateway_url():
    seen = []

    async def handler(request):
        seen.append(request)
        payload = {"id": "r", "status": "completed", "model": "gpt-6-luna", "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "요약"}]}]}
        return httpx.Response(200, json=payload)

    assert await gateway(handler).summarize([{"role": "user", "content": "a"}]) == "요약"
    assert [str(r.url) for r in seen] == ["https://api.baze.co/v1/gateway/responses/"]
    assert "truncation" not in json.loads(seen[0].content)


async def test_gateway_web_search_is_refused_before_sending():
    def handler(request):
        raise AssertionError("보내면 안 된다")

    with pytest.raises(ProviderError) as caught:
        await gateway(handler).web_search("칠보")
    assert caught.value.code == "SEARCH_NOT_SUPPORTED"


async def test_key_check_uses_the_configured_base(monkeypatch):
    from app.routers import openai_test

    seen = []

    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "gpt-6-luna"}]})

    result = await openai_test.check_openai_key("k", httpx.MockTransport(handler), GATEWAY)
    assert result.ok and seen == ["https://api.baze.co/v1/gateway/models/"]
