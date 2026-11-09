from datetime import datetime, timedelta, timezone

import jwt
from bson import ObjectId

from app.config import get_settings

CREDENTIALS = {"email": "a@test.com", "password": "password123", "creator_name": "창작자"}


async def test_signup_sets_httponly_cookie_and_hides_hash(client, db):
    res = await client.post("/auth/signup", json=CREDENTIALS)
    assert res.status_code == 201
    cookie = res.headers["set-cookie"].lower()
    assert "access_token=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "password_hash" not in res.text and "password123" not in res.text
    assert set(res.json()) == {"id", "email", "creator_name", "created_at", "updated_at"}
    saved = await db.users.find_one({"email": "a@test.com"})
    assert saved["password_hash"].startswith("$argon2id$")


async def test_email_is_normalized_and_duplicate_rejected(client):
    assert (await client.post("/auth/signup", json={**CREDENTIALS, "email": "A@Test.com"})).json()["email"] == "a@test.com"
    res = await client.post("/auth/signup", json=CREDENTIALS)
    assert res.status_code == 409
    assert res.json()["code"] == "EMAIL_TAKEN"


async def test_password_length_rules(client):
    for password in ("x" * 7, "x" * 129):
        res = await client.post("/auth/signup", json={**CREDENTIALS, "password": password})
        assert res.status_code == 422


async def test_blank_creator_name_rejected(client):
    res = await client.post("/auth/signup", json={**CREDENTIALS, "creator_name": "   "})
    assert res.status_code == 422


async def test_login_failure_message_is_same(client, login):
    await login(client)
    client.cookies.clear()
    wrong_password = await client.post("/auth/login", json={"email": "a@test.com", "password": "wrong-password"})
    no_user = await client.post("/auth/login", json={"email": "none@test.com", "password": "wrong-password"})
    assert wrong_password.status_code == no_user.status_code == 401
    assert wrong_password.json()["code"] == "INVALID_CREDENTIALS"
    assert wrong_password.json() == no_user.json()


async def test_login_then_me(client, login):
    await login(client)
    client.cookies.clear()
    assert (await client.get("/auth/me")).status_code == 401
    res = await client.post("/auth/login", json={"email": "A@test.com", "password": "password123"})
    assert res.status_code == 200
    assert (await client.get("/auth/me")).json()["email"] == "a@test.com"


async def test_logout_then_me_is_401(client, login):
    await login(client)
    assert (await client.post("/auth/logout")).status_code == 204
    res = await client.get("/auth/me")
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHORIZED"


def make_token(sub, secret=None, expires_in=3600):
    payload = {"sub": sub, "exp": datetime.now(timezone.utc) + timedelta(seconds=expires_in)}
    return jwt.encode(payload, secret or get_settings().jwt_secret, algorithm="HS256")


async def test_tampered_expired_and_deleted_user_tokens_are_401(client, login, db):
    user = await login(client)
    bad_tokens = [
        client.cookies["access_token"] + "x",
        "not-a-token",
        make_token(user["id"], secret="another-secret-at-least-32-bytes-long"),
        make_token(user["id"], expires_in=-10),
        make_token("not-an-object-id"),
        make_token(str(ObjectId())),
    ]
    for token in bad_tokens:
        client.cookies.set("access_token", token)
        assert (await client.get("/auth/me")).status_code == 401, token


async def test_missing_jwt_secret_returns_503(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "jwt_secret", "")
    res = await client.post("/auth/signup", json=CREDENTIALS)
    assert res.status_code == 503
    assert res.json()["code"] == "AUTH_NOT_CONFIGURED"


async def test_missing_mongo_returns_503(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "mongo_details", "")
    res = await client.post("/auth/login", json={"email": "a@test.com", "password": "password123"})
    assert res.status_code == 503
    assert res.json()["code"] == "DB_NOT_CONFIGURED"


async def test_patch_profile_changes_creator_name_only(client, login):
    await login(client)
    res = await client.patch("/profile", json={"creator_name": "  새 이름 "})
    assert res.status_code == 200
    assert res.json()["creator_name"] == "새 이름"
    assert (await client.patch("/profile", json={"email": "b@test.com"})).status_code == 422
    client.cookies.clear()
    assert (await client.patch("/profile", json={"creator_name": "x"})).status_code == 401
