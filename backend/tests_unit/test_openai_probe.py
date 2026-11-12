import httpx
import pytest

from app.ai.responses import ProviderConfig, ResponsesClient
from devtools.openai_probe import app, get_provider

ORIGIN = {"Origin": "http://127.0.0.1:8765"}


@pytest.fixture
def test_provider():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "resp_test", "status": "completed", "model": "gpt-6-luna", "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "합성 응답"}]}
        ]})

    app.dependency_overrides[get_provider] = lambda: ResponsesClient(ProviderConfig("fake-key"), httpx.MockTransport(handler))
    yield seen
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_page_does_not_generate_and_probe_runs_in_process(test_provider, monkeypatch):
    monkeypatch.delenv("AX_OPENAI_PROBE_LIVE", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        page = await client.get("/")
        mode = await client.get("/api/mode")
        assert page.status_code == 200
        assert "OpenAI 송수신 확인" in page.text
        assert mode.json() == {"mode": "mock"}
        assert test_provider == []
        response = await client.post("/api/probe", headers=ORIGIN, json={"text": "합성 요청"})
    assert response.status_code == 200
    assert response.json()["text"] == "합성 응답"
    assert len(test_provider) == 1


@pytest.mark.asyncio
async def test_fake_mode_works_without_environment_key(monkeypatch):
    monkeypatch.delenv("AX_OPENAI_PROBE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        result = await client.post("/api/probe", headers=ORIGIN, json={"text": "hello"})
    assert result.status_code == 200
    assert "모의 응답" in result.json()["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Origin": "https://other.example"}, {"Origin": "null"}])
async def test_cross_origin_posts_do_not_dispatch(headers, test_provider):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        result = await client.post("/api/probe", headers=headers, json={"text": "hi"})
    assert result.status_code == 403
    assert test_provider == []


@pytest.mark.asyncio
async def test_non_local_host_and_remote_peer_are_rejected():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://evil.example") as client:
        assert (await client.get("/")).status_code == 403
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("192.0.2.3", 40000)), base_url="http://127.0.0.1:8765") as client:
        assert (await client.get("/")).status_code == 403


@pytest.mark.asyncio
async def test_live_mode_missing_key_is_safe(monkeypatch):
    monkeypatch.setenv("AX_OPENAI_PROBE_LIVE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        assert (await client.get("/api/mode")).json() == {"mode": "live"}
        result = await client.post("/api/probe", headers=ORIGIN, json={"text": "hello"})
    assert result.status_code == 502
    assert result.json() == {"code": "KEY_MISSING", "delivery": "not_dispatched"}


@pytest.mark.asyncio
async def test_validation_error_does_not_echo_unexpected_fields():
    secret = "test-secret-do-not-echo"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        result = await client.post("/api/probe", headers=ORIGIN, json={"text": "hello", "api_key": secret})
    assert result.status_code == 422
    assert secret not in result.text
    assert result.json() == {"code": "INVALID_INPUT"}
