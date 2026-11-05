from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import get_settings
from app.errors import AppError

DEFAULT_DB_NAME = "ax_platform"

_client: AsyncMongoClient | None = None


def get_db() -> AsyncDatabase:
    global _client
    uri = get_settings().mongo_details
    if not uri:
        raise AppError(503, "DB_NOT_CONFIGURED", "DB 연결이 설정되지 않았습니다.")
    if _client is None:
        _client = AsyncMongoClient(uri, serverSelectionTimeoutMS=10000, tz_aware=True)
    return _client.get_default_database(default=DEFAULT_DB_NAME)


async def close_db() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
