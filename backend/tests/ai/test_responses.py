import json
import logging

import httpx
import pytest

from app.ai.responses import (
    Delivery, GenerationRequest, ProviderConfig, ProviderError, ResponsesClient, parse_response,
)

FAKE_KEY = "sk-test-only-secret"


def response_payload(text="안녕하세요"):
    return {
        "id": "resp_test", "status": "completed", "model": "gpt-6-luna",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 10, "output_tokens": 3},
    }


def test_environment_key_and_safe_representation(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    config = ProviderConfig.from_environment()
    assert config.api_key == FAKE_KEY
    assert FAKE_KEY not in repr(config)
    assert "private input" not in repr(GenerationRequest("private input"))


@pytest.mark.asyncio
@pytest.mark.parametrize("key,expected", [("", "KEY_MISSING"), ("  ", "KEY_MISSING"), ("key\nsecret", "KEY_INVALID_FORMAT")])
async def test_bad_key_rejected_before_dispatch(key, expected):
    calls = []
    provider = ResponsesClient(ProviderConfig(key), httpx.MockTransport(lambda r: calls.append(r)))
    with pytest.raises(ProviderError) as error:
        await provider.generate(GenerationRequest("hello"))
    assert error.value.code == expected
    assert error.value.delivery == Delivery.NOT_DISPATCHED
    assert calls == []


@pytest.mark.asyncio
async def test_real_serializer_headers_and_parser():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response_payload())

    result = await ResponsesClient(ProviderConfig(FAKE_KEY), httpx.MockTransport(handler)).generate(
        GenerationRequest("hello", max_output_tokens=128)
    )
    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.openai.com/v1/responses"
    assert seen[0].method == "POST"
    assert seen[0].headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert json.loads(seen[0].content) == {
        "model": "gpt-6-luna", "input": "hello", "max_output_tokens": 128,
        "stream": False, "store": False, "background": False,
        "reasoning": {"effort": "none"}, "truncation": "disabled",
    }
    assert result.text == "안녕하세요"
    assert result.response_id == "resp_test"
    assert result.model == "gpt-6-luna"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (10, 3)


def test_multiple_output_parts_reasoning_and_missing_usage():
    result = parse_response({
        "id": "resp_test", "status": "completed", "model": "gpt-6-luna", "output": [
            {"type": "reasoning"},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "첫 "}, {"type": "output_text", "text": "응답"},
            ]},
            {"type": "reasoning"},
        ],
    })
    assert result.text == "첫 응답"
    assert result.usage is None


@pytest.mark.parametrize("payload,expected", [
    ({"status": "incomplete"}, "INCOMPLETE"),
    ({"status": "failed"}, "PROVIDER_FAILED"),
    ({"status": "completed", "model": "gpt-6-luna", "output": []}, "INVALID_RESPONSE"),
    ({"id": "r", "status": "completed", "model": "gpt-6-luna", "output": []}, "EMPTY_OUTPUT"),
    ({"id": "r", "status": "completed", "model": "gpt-6-luna", "output": [{"type": "function_call"}]}, "INVALID_RESPONSE"),
    ({"id": "r", "status": "completed", "model": "gpt-6-luna", "output": [{"type": "message", "role": "user", "content": []}]}, "INVALID_RESPONSE"),
    ({"id": "r", "status": "completed", "model": "gpt-6-luna", "output": [{"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "no"}]}]}, "REFUSED"),
    ({"id": "r", "status": "completed", "model": "gpt-6-luna", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": " "}]}]}, "EMPTY_OUTPUT"),
    ({"id": "r", "status": "completed", "model": "gpt-6-luna", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}], "usage": {"input_tokens": True, "output_tokens": 1}}, "INVALID_RESPONSE"),
    ({"status": 12}, "INVALID_RESPONSE"),
    ({"status": "in_progress"}, "INVALID_RESPONSE"),
])
def test_invalid_or_noncomplete_responses(payload, expected):
    with pytest.raises(ProviderError) as error:
        parse_response(payload)
    assert error.value.code == expected
    assert error.value.delivery == Delivery.RESPONDED


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status,expected", [(401, "AUTH_ERROR"), (403, "AUTH_ERROR"), (429, "RATE_LIMITED"), (500, "UPSTREAM_ERROR")])
async def test_status_errors_are_sanitized_and_not_retried(http_status, expected, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(http_status, json={"error": {"message": FAKE_KEY}})

    provider = ResponsesClient(ProviderConfig(FAKE_KEY), httpx.MockTransport(handler))
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as error:
        await provider.generate(GenerationRequest("hello"))
    assert error.value.code == expected
    assert error.value.delivery == Delivery.RESPONDED
    assert FAKE_KEY not in str(error.value)
    assert FAKE_KEY not in caplog.text
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("exception,code,delivery", [
    (httpx.ConnectError, "CONNECTION_FAILED", Delivery.NOT_DISPATCHED),
    (httpx.ConnectTimeout, "CONNECTION_FAILED", Delivery.NOT_DISPATCHED),
    (httpx.ReadTimeout, "TIMEOUT", Delivery.UNKNOWN),
    (httpx.WriteError, "TRANSPORT_ERROR", Delivery.UNKNOWN),
])
async def test_transport_errors_do_not_disclose_or_retry(exception, code, delivery):
    calls = []

    def handler(request):
        calls.append(request)
        raise exception(FAKE_KEY, request=request)

    with pytest.raises(ProviderError) as error:
        await ResponsesClient(ProviderConfig(FAKE_KEY), httpx.MockTransport(handler)).generate(GenerationRequest("hi"))
    assert (error.value.code, error.value.delivery) == (code, delivery)
    assert FAKE_KEY not in str(error.value)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_invalid_json_is_not_success():
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text="not json"))
    with pytest.raises(ProviderError) as error:
        await ResponsesClient(ProviderConfig(FAKE_KEY), transport).generate(GenerationRequest("hello"))
    assert error.value.code == "INVALID_RESPONSE"


@pytest.mark.asyncio
async def test_upstream_echo_of_key_is_redacted():
    payload = response_payload(f"echo: {FAKE_KEY}")
    payload["id"] = FAKE_KEY
    result = await ResponsesClient(ProviderConfig(FAKE_KEY), httpx.MockTransport(lambda _: httpx.Response(200, json=payload))).generate(GenerationRequest("hi"))
    assert FAKE_KEY not in result.text
    assert FAKE_KEY not in result.response_id


@pytest.mark.asyncio
async def test_model_mismatch_is_not_success():
    payload = response_payload()
    payload["model"] = "another-model"
    with pytest.raises(ProviderError) as error:
        await ResponsesClient(ProviderConfig(FAKE_KEY), httpx.MockTransport(lambda _: httpx.Response(200, json=payload))).generate(GenerationRequest("hi"))
    assert error.value.code == "MODEL_MISMATCH"


@pytest.mark.asyncio
async def test_product_output_budget_is_not_limited_by_probe_default():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=response_payload())

    await ResponsesClient(ProviderConfig(FAKE_KEY), httpx.MockTransport(handler)).generate(
        GenerationRequest("hi", max_output_tokens=8000)
    )
    assert bodies[0]["max_output_tokens"] == 8000


@pytest.mark.asyncio
@pytest.mark.parametrize("generation_request", [GenerationRequest(" "), GenerationRequest("hi", 15), GenerationRequest("hi", True)])
async def test_invalid_request_does_not_dispatch(generation_request):
    calls = []
    with pytest.raises(ProviderError) as error:
        await ResponsesClient(ProviderConfig(FAKE_KEY), httpx.MockTransport(lambda r: calls.append(r))).generate(generation_request)
    assert error.value.code == "INVALID_INPUT"
    assert calls == []
