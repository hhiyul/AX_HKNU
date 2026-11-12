"""Local-only probe: fake by default; live mode requires explicit opt-in."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.ai.responses import GenerationRequest, ProviderConfig, ProviderError, ResponsesClient

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
HTML = Path(__file__).with_name("openai_probe.html")


def live_enabled() -> bool:
    return os.environ.get("AX_OPENAI_PROBE_LIVE") == "1"


def get_provider() -> ResponsesClient:
    if live_enabled():
        return ResponsesClient(ProviderConfig.from_environment())

    def fake_response(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "resp_local_mock", "status": "completed", "model": "gpt-6-luna",
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "모의 응답: 백엔드의 요청과 응답 경로가 연결되었습니다."}
            ]}],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        })

    return ResponsesClient(ProviderConfig(api_key="local-test-key"), httpx.MockTransport(fake_response))


@app.middleware("http")
async def local_only(request: Request, call_next):
    try:
        peer = ipaddress.ip_address(request.client.host) if request.client else None
        host_header = request.headers.get("host", "")
        host = urlsplit("http://" + host_header).hostname
        origin = request.headers.get("origin")
        allowed = peer is not None and peer.is_loopback and host in ("localhost", "127.0.0.1", "::1")
        if request.method not in ("GET", "HEAD"):
            allowed = allowed and origin == f"http://{host_header}"
    except ValueError:
        allowed = False
    if not allowed:
        return JSONResponse({"code": "LOCAL_ONLY"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    # Never reflect invalid input or unexpected fields back to the browser.
    return JSONResponse({"code": "INVALID_INPUT"}, status_code=422)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(HTML.read_text(encoding="utf-8"))


@app.get("/api/mode")
async def mode() -> dict[str, str]:
    return {"mode": "live" if live_enabled() else "mock"}


@app.post("/api/probe")
async def probe(body: ProbeRequest, provider: ResponsesClient = Depends(get_provider)):
    try:
        result = await provider.generate(GenerationRequest(text=body.text))
    except ProviderError as exc:
        return JSONResponse({"code": exc.code, "delivery": exc.delivery.value}, status_code=502)
    return {"text": result.text, "model": result.model, "usage": vars(result.usage) if result.usage else None}
