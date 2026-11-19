"""Responses API adapter with injected transport and no database dependency."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from enum import Enum

import httpx

RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-6-luna"


class Delivery(str, Enum):
    NOT_DISPATCHED = "not_dispatched"
    UNKNOWN = "unknown"
    RESPONDED = "responded"


class ProviderError(Exception):
    """A sanitized error; raw provider payloads and exceptions are not retained."""

    def __init__(self, code: str, delivery: Delivery):
        self.code = code
        self.delivery = delivery
        super().__init__(code)


@dataclass(frozen=True)
class ProviderConfig:
    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> ProviderConfig:
        return cls(api_key=os.environ.get("OPENAI_API_KEY", ""))


@dataclass(frozen=True)
class GenerationRequest:
    text: str = field(repr=False)
    max_output_tokens: int = 128


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class GenerationResult:
    text: str = field(repr=False)
    response_id: str
    model: str
    usage: Usage | None = None


def _usage(value: object) -> Usage | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProviderError("INVALID_RESPONSE", Delivery.RESPONDED)
    input_tokens, output_tokens = value.get("input_tokens"), value.get("output_tokens")
    if any(type(n) is not int or n < 0 for n in (input_tokens, output_tokens)):
        raise ProviderError("INVALID_RESPONSE", Delivery.RESPONDED)
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens)


def parse_response(payload: object) -> GenerationResult:
    if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
        raise ProviderError("INVALID_RESPONSE", Delivery.RESPONDED)
    status = payload["status"]
    if status == "incomplete":
        raise ProviderError("INCOMPLETE", Delivery.RESPONDED)
    if status == "failed":
        raise ProviderError("PROVIDER_FAILED", Delivery.RESPONDED)
    if status != "completed":
        raise ProviderError("INVALID_RESPONSE", Delivery.RESPONDED)
    response_id, model, output = payload.get("id"), payload.get("model"), payload.get("output")
    if (not isinstance(response_id, str) or not response_id.strip() or
            not isinstance(model, str) or not model.strip() or not isinstance(output, list)):
        raise ProviderError("INVALID_RESPONSE", Delivery.RESPONDED)

    chunks: list[str] = []
    refused = False
    for item in output:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise ProviderError("INVALID_RESPONSE", Delivery.RESPONDED)
        if item["type"] == "reasoning":
            continue
        if item["type"] != "message" or item.get("role") != "assistant":
            # No tools are registered in this profile; never execute tool calls.
            raise ProviderError("INVALID_RESPONSE", Delivery.RESPONDED)
        content = item.get("content")
        if not isinstance(content, list):
            raise ProviderError("INVALID_RESPONSE", Delivery.RESPONDED)
        for part in content:
            if not isinstance(part, dict):
                raise ProviderError("INVALID_RESPONSE", Delivery.RESPONDED)
            if part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                refused = True
            elif part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
            else:
                raise ProviderError("INVALID_RESPONSE", Delivery.RESPONDED)
    if refused:
        raise ProviderError("REFUSED", Delivery.RESPONDED)
    text = "".join(chunks)
    if not text.strip():
        raise ProviderError("EMPTY_OUTPUT", Delivery.RESPONDED)
    return GenerationResult(text=text, response_id=response_id, model=model, usage=_usage(payload.get("usage")))


class ResponsesClient:
    def __init__(self, config: ProviderConfig, transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        self._transport = transport

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        key = self._config.api_key
        if not key or not key.strip():
            raise ProviderError("KEY_MISSING", Delivery.NOT_DISPATCHED)
        if any(not ("!" <= c <= "~") for c in key):
            raise ProviderError("KEY_INVALID_FORMAT", Delivery.NOT_DISPATCHED)
        if not isinstance(request.text, str) or not request.text.strip():
            raise ProviderError("INVALID_INPUT", Delivery.NOT_DISPATCHED)
        if type(request.max_output_tokens) is not int or not 16 <= request.max_output_tokens <= 128_000:
            raise ProviderError("INVALID_INPUT", Delivery.NOT_DISPATCHED)
        if not self._config.model.strip() or self._config.timeout_seconds <= 0:
            raise ProviderError("INVALID_CONFIG", Delivery.NOT_DISPATCHED)

        body = {
            "model": self._config.model,
            "input": request.text,
            "max_output_tokens": request.max_output_tokens,
            "stream": False,
            "store": False,
            "background": False,
            "reasoning": {"effort": "none"},
            "truncation": "disabled",
        }
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._config.timeout_seconds) as client:
                response = await client.post(RESPONSES_URL, headers={"Authorization": f"Bearer {key}"}, json=body)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise ProviderError("CONNECTION_FAILED", Delivery.NOT_DISPATCHED) from None
        except httpx.TimeoutException:
            raise ProviderError("TIMEOUT", Delivery.UNKNOWN) from None
        except httpx.HTTPError:
            raise ProviderError("TRANSPORT_ERROR", Delivery.UNKNOWN) from None

        if response.status_code in (401, 403):
            raise ProviderError("AUTH_ERROR", Delivery.RESPONDED)
        if response.status_code == 429:
            raise ProviderError("RATE_LIMITED", Delivery.RESPONDED)
        if not 200 <= response.status_code < 300:
            raise ProviderError("UPSTREAM_ERROR", Delivery.RESPONDED)
        try:
            payload = response.json()
        except ValueError:
            raise ProviderError("INVALID_RESPONSE", Delivery.RESPONDED) from None
        result = parse_response(payload)
        if result.model != self._config.model:
            raise ProviderError("MODEL_MISMATCH", Delivery.RESPONDED)
        # An upstream response can echo a credential; never return it to a caller.
        return replace(result, text=result.text.replace(key, "[REDACTED]"), response_id=result.response_id.replace(key, "[REDACTED]"))
