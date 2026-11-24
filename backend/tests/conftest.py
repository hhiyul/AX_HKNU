import os
from urllib.parse import urlparse

import httpx
import pytest
import pytest_asyncio

TEST_MONGO_HOST = "test-mongo"


def pytest_configure():
    # Atlas 등 실제 DB에 테스트가 쓰지 못하게 막는다.
    host = urlparse(os.environ.get("MONGO_DETAILS", "")).hostname
    if host != TEST_MONGO_HOST:
        pytest.exit(f"테스트는 {TEST_MONGO_HOST}에서만 실행합니다.", returncode=2)


def load_init_db():
    # init_db.py는 git에 없으므로, 없을 때 전부 건너뛰어 성공처럼 보이지 않게 멈춘다.
    try:
        import init_db
    except ImportError:
        pytest.exit("backend/init_db.py가 있어야 테스트를 실행할 수 있습니다.", returncode=2)
    return init_db


@pytest.fixture(scope="session", autouse=True)
def schema():
    init_db = load_init_db()
    from pymongo import MongoClient

    with MongoClient(os.environ["MONGO_DETAILS"]) as client:
        db = client.get_default_database()
        for name, rules in init_db.COLLECTIONS.items():
            init_db.apply_collection(db, name, rules)
            for keys, options in init_db.INDEXES.get(name, []):
                db[name].create_index(keys, **options)


@pytest_asyncio.fixture
async def db(schema):
    from app.db import get_db

    database = get_db()
    for name in await database.list_collection_names():
        await database[name].delete_many({})
    return database


@pytest_asyncio.fixture
async def client(db):
    from app.services import rate_limit
    from main import app

    # 로그인 실패 기록은 서버 메모리에 남으므로 테스트마다 비운다.
    rate_limit.emails.hits.clear()
    rate_limit.addresses.hits.clear()

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def signup(http, email="a@test.com", password="password123", creator_name="창작자"):
    res = await http.post(
        "/auth/signup", json={"email": email, "password": password, "creator_name": creator_name}
    )
    assert res.status_code == 201, res.text
    return res.json()


@pytest.fixture
def login():
    return signup
