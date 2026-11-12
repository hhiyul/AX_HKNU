"""Check production routing without starting MongoDB or using the network."""

import httpx
import pytest

from app.deps import current_user
from app.routers.openai_test import get_openai_transport
from main import app


@pytest.mark.asyncio
async def test_existing_key_check_and_production_route_are_unchanged():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"id": "example"}]})

    app.dependency_overrides[current_user] = lambda: {"_id": "test-user"}
    app.dependency_overrides[get_openai_transport] = lambda: httpx.MockTransport(handler)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.post("/openai/test", json={"api_key": "test-key"})
            not_exposed = await client.post("/api/probe", json={"text": "test"})
    finally:
        app.dependency_overrides.clear()
    assert result.json() == {"ok": True, "model_count": 1, "reason": None}
    assert result.status_code == 200
    assert not_exposed.status_code == 404
    assert len(calls) == 1
    assert str(calls[0].url) == "https://api.openai.com/v1/models"
