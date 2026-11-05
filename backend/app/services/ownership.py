from bson import ObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError


def not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "자료를 찾을 수 없습니다.")


def parse_object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise not_found() from None


async def get_owned_brand(db: AsyncDatabase, user_id: ObjectId, brand_id: str | ObjectId) -> dict:
    brand = await db.brands.find_one({"_id": parse_object_id(brand_id), "user_id": user_id})
    if brand is None:
        raise not_found()
    return brand


async def get_owned_project(db: AsyncDatabase, user_id: ObjectId, project_id: str) -> dict:
    # 상품에는 user_id가 없으므로 소속 브랜드로 판단한다.
    project = await db.projects.find_one({"_id": parse_object_id(project_id)})
    if project is None:
        raise not_found()
    await get_owned_brand(db, user_id, project["brand_id"])
    return project


async def get_owned_asset(db: AsyncDatabase, user_id: ObjectId, asset_id: str) -> dict:
    # 파일에도 user_id가 없으므로 소속 브랜드로 판단한다.
    asset = await db.assets.find_one({"_id": parse_object_id(asset_id)})
    if asset is None:
        raise not_found()
    await get_owned_brand(db, user_id, asset["brand_id"])
    return asset
