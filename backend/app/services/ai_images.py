"""채팅 메시지에 붙인 사진을 AI 이미지 입력(input_image)으로 바꾼다.

- 이번 메시지에 붙인 사진(PNG·JPEG·WebP, 업로드 때 파일 내용으로 확인한 것)만 보낸다. 앞선 메시지의 사진은 다시 보내지 않는다.
- 긴 변이 MAX_SIDE보다 크면 줄여서 보낸다(속도·사용량). 줄이지 못하면 원본을 그대로 보낸다.
- 한 메시지에 MAX_IMAGES장까지. 나머지와 사라진 파일은 건너뛴다.
"""
import asyncio
import base64
import io
import logging

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.storage import file_path

logger = logging.getLogger(__name__)

IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_IMAGES = 4
MAX_SIDE = 1568
JPEG_QUALITY = 85


def shrink(data: bytes, mime: str) -> tuple[bytes, str]:
    """긴 변을 MAX_SIDE로 줄인다. 투명한 부분이 있으면 PNG, 아니면 JPEG로 다시 쓴다. 실패하면 원본."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if max(image.size) <= MAX_SIDE:
                return data, mime
            image.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            if image.mode in ("RGBA", "LA", "P"):
                image.save(out, format="PNG", optimize=True)
                return out.getvalue(), "image/png"
            image.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return out.getvalue(), "image/jpeg"
    except Exception:  # 손상된 그림·모르는 형식은 줄이지 않고 원본을 보낸다.
        logger.warning("Could not shrink image; sending original", exc_info=True)
        return data, mime


def _read(asset_id: ObjectId) -> bytes | None:
    path = file_path(asset_id)
    return path.read_bytes() if path.is_file() else None


async def image_inputs(db: AsyncDatabase, user_id: ObjectId, asset_ids: list[ObjectId]) -> tuple[list[dict], list[ObjectId]]:
    """(AI에 보낼 input_image 목록, 보낸 사진의 asset id). 내 파일이고 사진인 것만, 붙인 순서대로."""
    if not asset_ids:
        return [], []
    query = {"_id": {"$in": list(asset_ids)}, "user_id": user_id, "mime_type": {"$in": list(IMAGE_TYPES)}}
    found = {asset["_id"]: asset async for asset in db.assets.find(query, {"mime_type": 1})}
    inputs, used = [], []
    for ident in asset_ids:
        asset = found.get(ident)
        if asset is None or len(used) >= MAX_IMAGES:
            continue
        data = await asyncio.to_thread(_read, ident)
        if not data:
            continue
        data, mime = await asyncio.to_thread(shrink, data, asset["mime_type"])
        url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        inputs.append({"type": "input_image", "image_url": url, "detail": "auto"})
        used.append(ident)
    return inputs, used
