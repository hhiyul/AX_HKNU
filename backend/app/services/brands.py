from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.schemas.brands import BrandCreate, BrandUpdate
from app.services.store import get_owned_brand, not_found, parse_object_id, remove_jobs


def ensure_completable(brand: dict, changes: dict) -> None:
    merged = {**brand, **changes}
    if merged.get("status") == "completed" and not merged.get("name"):
        raise AppError(422, "NAME_REQUIRED", "완료하려면 이름을 입력해 주세요.")


async def save_changes(db: AsyncDatabase, brand: dict, changes: dict) -> dict:
    if not changes:
        return brand
    updated = await db.brands.find_one_and_update(
        {"_id": brand["_id"]},
        {"$set": {**changes, "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        # 수정하는 사이에 다른 요청이 지운 경우
        raise not_found()
    return updated


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


async def list_brands(db: AsyncDatabase, user_id: ObjectId, page) -> list[dict]:
    cursor = db.brands.find({"user_id": user_id}).sort("created_at", -1)
    return await cursor.skip(page.skip).limit(page.limit).to_list()


async def update_brand(db: AsyncDatabase, user_id: ObjectId, brand_id: str, body: BrandUpdate) -> dict:
    brand = await get_owned_brand(db, user_id, brand_id)
    changes = body.model_dump(exclude_unset=True)
    ensure_completable(brand, changes)
    if changes.get("logo_asset_id") is not None:
        logo_id = parse_object_id(changes["logo_asset_id"])
        logo = await db.assets.find_one({"_id": logo_id, "brand_id": brand["_id"]})
        if logo is None:
            raise not_found()
        if not logo["mime_type"].startswith("image/"):
            raise AppError(422, "LOGO_NOT_IMAGE", "로고는 이미지 파일만 지정할 수 있습니다.")
        changes["logo_asset_id"] = logo_id
    return await save_changes(db, brand, changes)


async def delete_brand(db: AsyncDatabase, user_id: ObjectId, brand_id: str) -> None:
    brand = await get_owned_brand(db, user_id, brand_id)
    in_use = {"brand_id": brand["_id"]}
    if await db.projects.find_one(in_use) or await db.assets.find_one(in_use):
        raise AppError(409, "BRAND_IN_USE", "브랜드에 상품이나 파일이 남아 있습니다.")
    await remove_jobs(db, in_use, AppError(409, "BRAND_IN_USE", "브랜드에 진행 중인 작업이 있습니다."))
    await db.brands.delete_one({"_id": brand["_id"]})
