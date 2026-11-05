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
