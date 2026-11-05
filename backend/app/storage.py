"""업로드 파일 저장. 저장소를 바꿀 때는 이 파일만 고친다."""
import asyncio
from pathlib import Path

from bson import ObjectId

from app.config import get_settings


def file_path(asset_id: ObjectId) -> Path:
    # 사용자가 보낸 이름은 쓰지 않고 문서 ID를 파일 이름으로 쓴다.
    return Path(get_settings().upload_dir) / str(asset_id)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def save_file(asset_id: ObjectId, data: bytes) -> None:
    await asyncio.to_thread(_write, file_path(asset_id), data)


async def delete_file(asset_id: ObjectId) -> None:
    await asyncio.to_thread(file_path(asset_id).unlink, missing_ok=True)
