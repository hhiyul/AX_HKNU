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


@pytest.fixture(scope="session", autouse=True)
def schema():
    init_db = pytest.importorskip("init_db")
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
    from main import app

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
