from bson import ObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError


def not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "자료를 찾을 수 없습니다.")


def parse_object_id(value: str | ObjectId) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise not_found() from None


async def get_owned(db: AsyncDatabase, collection: str, user_id: ObjectId, doc_id: str | ObjectId, **extra) -> dict:
    """내 자료만 꺼낸다. 남의 자료와 잘못된 ID는 모두 404."""
    document = await db[collection].find_one({"_id": parse_object_id(doc_id), "user_id": user_id, **extra})
    if document is None:
        raise not_found()
    return document


async def get_owned_brand(db: AsyncDatabase, user_id: ObjectId, brand_id: str | ObjectId) -> dict:
    return await get_owned(db, "brands", user_id, brand_id)


async def get_owned_project(db: AsyncDatabase, user_id: ObjectId, project_id: str | ObjectId) -> dict:
    return await get_owned(db, "projects", user_id, project_id)


async def get_owned_asset(db: AsyncDatabase, user_id: ObjectId, asset_id: str | ObjectId) -> dict:
    return await get_owned(db, "assets", user_id, asset_id)
