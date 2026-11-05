async def test_hello_still_works(client):
    assert (await client.get("/hello/hknu")).json() == {"message": "Hello hknu"}


async def test_unknown_route_uses_common_error_format(client):
    res = await client.get("/no-such-route")
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"
    assert res.json()["message"]


async def test_validation_error_format_hides_input(client):
    res = await client.post("/auth/login", json={"email": "not-an-email", "password": "SECRET-INPUT"})
    assert res.status_code == 422
    assert res.json()["code"] == "VALIDATION_ERROR"
    assert "SECRET-INPUT" not in res.text


def test_missing_init_db_stops_tests_instead_of_skipping(monkeypatch):
    import sys

    import pytest
    from conftest import load_init_db

    monkeypatch.setitem(sys.modules, "init_db", None)
    with pytest.raises(pytest.exit.Exception):
        load_init_db()


async def test_unreachable_db_returns_503(client):
    from pymongo.errors import ServerSelectionTimeoutError

    from app.db import get_db
    from main import app

    class Unreachable:
        def __getattr__(self, name):
            return self

        async def find_one(self, *args):
            raise ServerSelectionTimeoutError("no servers")

    app.dependency_overrides[get_db] = lambda: Unreachable()
    try:
        res = await client.post("/auth/login", json={"email": "a@test.com", "password": "password123"})
    finally:
        app.dependency_overrides.pop(get_db)
    assert res.status_code == 503
    assert res.json()["code"] == "DB_UNAVAILABLE"
    assert "no servers" not in res.text
