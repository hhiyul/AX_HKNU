import logging

import httpx
import pytest

KEY = "sk-test-SECRET-KEY-VALUE"


@pytest.fixture
def openai(client):
    """실제 OpenAI를 호출하지 않도록 가짜 응답으로 바꾼다."""
    from app.routers.openai_test import get_openai_transport
    from main import app

    def use(handler):
        app.dependency_overrides[get_openai_transport] = lambda: httpx.MockTransport(handler)

    yield use
    app.dependency_overrides.pop(get_openai_transport, None)


async def test_openai_requires_login(client, openai):
    openai(lambda request: httpx.Response(200, json={"data": []}))
    assert (await client.post("/openai/test", json={"api_key": KEY})).status_code == 401


async def test_openai_success(client, login, openai):
    await login(client)
    openai(lambda request: httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]}))
    res = await client.post("/openai/test", json={"api_key": KEY})
    assert res.status_code == 200
    assert res.json() == {"ok": True, "model_count": 2, "reason": None}


async def test_openai_invalid_key(client, login, openai):
    await login(client)
    openai(lambda request: httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}}))
    res = await client.post("/openai/test", json={"api_key": KEY})
    assert res.json() == {"ok": False, "model_count": None, "reason": "invalid_key"}
    assert KEY not in res.text


async def test_openai_timeout(client, login, openai):
    await login(client)

    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    openai(handler)
    assert (await client.post("/openai/test", json={"api_key": KEY})).json()["reason"] == "timeout"


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500, json={}), httpx.Response(429, json={}), httpx.Response(200, text="not json")],
)
async def test_openai_upstream_error(client, login, openai, response):
    await login(client)
    openai(lambda request: response)
    assert (await client.post("/openai/test", json={"api_key": KEY})).json()["reason"] == "upstream_error"


async def test_openai_connection_error(client, login, openai):
    await login(client)

    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    openai(handler)
    assert (await client.post("/openai/test", json={"api_key": KEY})).json()["reason"] == "upstream_error"


async def test_openai_only_calls_models_endpoint(client, login, openai):
    await login(client)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    openai(handler)
    await client.post("/openai/test", json={"api_key": KEY})
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert str(seen[0].url) == "https://api.openai.com/v1/models"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"


@pytest.mark.parametrize("bad_key", ["키-한글-SECRET", "sk-SECRET\nX-Evil: 1", "sk SECRET", "", "x" * 513])
async def test_openai_bad_key_format_is_422_and_hidden(client, login, openai, bad_key):
    await login(client)
    openai(lambda request: pytest.fail("잘못된 키로 호출하면 안 됩니다."))
    res = await client.post("/openai/test", json={"api_key": bad_key})
    assert res.status_code == 422
    if bad_key:
        assert bad_key not in res.text


async def test_openai_key_not_stored_or_logged(client, login, openai, db, caplog):
    await login(client)
    openai(lambda request: httpx.Response(200, json={"data": []}))
    with caplog.at_level(logging.DEBUG):
        res = await client.post("/openai/test", json={"api_key": KEY})
    assert KEY not in res.text
    assert KEY not in caplog.text
    for name in await db.list_collection_names():
        async for document in db[name].find():
            assert KEY not in str(document)
