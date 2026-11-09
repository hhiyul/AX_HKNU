from datetime import datetime, timezone

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.schemas.brands import BrandCreate, BrandUpdate
from app.services.drafts import ensure_completable, save_changes
from app.services.ownership import get_owned_brand, not_found, parse_object_id


async def create_brand(db: AsyncDatabase, user_id: ObjectId, body: BrandCreate) -> dict:
    now = datetime.now(timezone.utc)
    brand = {
        **body.model_dump(),
        "user_id": user_id,
        "logo_asset_id": None,
        "status": "draft",
        "created_at": now,
        "updated_at": now,
    }
    await db.brands.insert_one(brand)
    return brand


async def list_brands(db: AsyncDatabase, user_id: ObjectId) -> list[dict]:
    return await db.brands.find({"user_id": user_id}).sort("created_at", -1).to_list()


async def update_brand(db: AsyncDatabase, user_id: ObjectId, brand_id: str, body: BrandUpdate) -> dict:
    brand = await get_owned_brand(db, user_id, brand_id)
    changes = body.model_dump(exclude_unset=True)
    ensure_completable(brand, changes)
    if changes.get("logo_asset_id") is not None:
        logo_id = parse_object_id(changes["logo_asset_id"])
        if await db.assets.find_one({"_id": logo_id, "brand_id": brand["_id"]}) is None:
            raise not_found()
        changes["logo_asset_id"] = logo_id
    return await save_changes(db.brands, brand, changes)


async def delete_brand(db: AsyncDatabase, user_id: ObjectId, brand_id: str) -> None:
    brand = await get_owned_brand(db, user_id, brand_id)
    in_use = {"brand_id": brand["_id"]}
    if await db.projects.find_one(in_use) or await db.assets.find_one(in_use):
        raise AppError(409, "BRAND_IN_USE", "브랜드에 상품이나 파일이 남아 있습니다.")
    await db.brands.delete_one({"_id": brand["_id"]})
