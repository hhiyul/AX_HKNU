import re
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import UploadFile
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.services.ownership import get_owned_asset, get_owned_brand, get_owned_project, not_found
from app.storage import delete_file, save_file

MAX_FILE_SIZE = 2 * 1024 * 1024
MAX_NAME_LENGTH = 255


def detect_mime_type(data: bytes, declared_type: str | None) -> str | None:
    """보낸 쪽이 알린 형식을 믿지 않고 파일 내용으로 판별한다."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if not data or not (declared_type or "").startswith("text/plain") or b"\x00" in data:
        return None
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return "text/plain"


def clean_file_name(name: str | None) -> str:
    # 경로를 떼고 제어 문자를 지운다. 표시용으로만 쓰는 값이다.
    base = re.split(r"[/\\]", name or "")[-1]
    return re.sub(r"[\x00-\x1f\x7f]", "", base).strip()[-MAX_NAME_LENGTH:] or "file"


async def create_asset(
    db: AsyncDatabase,
    user_id: ObjectId,
    file: UploadFile,
    brand_id: str,
    asset_type: str,
    project_id: str | None,
) -> dict:
    brand = await get_owned_brand(db, user_id, brand_id)
    project = await get_owned_project(db, user_id, project_id) if project_id else None
    if project and project["brand_id"] != brand["_id"]:
        raise not_found()

    data = await file.read(MAX_FILE_SIZE + 1)
    if len(data) > MAX_FILE_SIZE:
        raise AppError(413, "FILE_TOO_LARGE", "파일은 2MB까지 올릴 수 있습니다.")
    mime_type = detect_mime_type(data, file.content_type)
    if mime_type is None:
        raise AppError(422, "INVALID_ASSET", "TXT, PNG, JPEG, WebP 파일만 올릴 수 있습니다.")
    if asset_type != "document" and not mime_type.startswith("image/"):
        raise AppError(422, "INVALID_ASSET", "로고·배너·상품 이미지는 PNG, JPEG, WebP 파일만 올릴 수 있습니다.")

    asset_id = ObjectId()
    now = datetime.now(timezone.utc)
    asset = {
        "_id": asset_id,
        "brand_id": brand["_id"],
        "project_id": project["_id"] if project else None,
        "ai_job_id": None,
        "asset_type": asset_type,
        "source": "upload",
        "file_url": f"/assets/{asset_id}/file",
        "file_name": clean_file_name(file.filename),
        "mime_type": mime_type,
        "created_at": now,
        "updated_at": now,
    }
    await save_file(asset_id, data)
    try:
        await db.assets.insert_one(asset)
    except Exception:
        await delete_file(asset_id)
        raise
    return asset


async def list_assets(db: AsyncDatabase, user_id: ObjectId, brand_id: str, page) -> list[dict]:
    brand = await get_owned_brand(db, user_id, brand_id)
    cursor = db.assets.find({"brand_id": brand["_id"]}).sort("created_at", -1)
    return await cursor.skip(page.skip).limit(page.limit).to_list()


async def delete_asset(db: AsyncDatabase, user_id: ObjectId, asset_id: str) -> None:
    asset = await get_owned_asset(db, user_id, asset_id)
    await db.brands.update_many({"logo_asset_id": asset["_id"]}, {"$set": {"logo_asset_id": None}})
    await db.assets.delete_one({"_id": asset["_id"]})
    await delete_file(asset["_id"])
