from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import FileResponse
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.deps import Page, current_user
from app.errors import AppError
from app.schemas.assets import AssetOut, AssetType
from app.schemas.common import to_api
from app.services import assets as service
from app.services.store import get_owned_asset
from app.storage import file_path

router = APIRouter(prefix="/assets", tags=["assets"])

# 여기는 올린 파일만 다룬다. 기획 산출물(문서)은 /projects/{id}/items/document 에 있다.


@router.get("", response_model=list[AssetOut])
async def list_assets(
    brand_id: str | None = None,
    project_id: str | None = None,
    page: Page = Depends(),
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    return to_api(await service.list_assets(db, user["_id"], brand_id, project_id, page))


@router.post("", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    file: UploadFile = File(),
    brand_id: str | None = Form(default=None),
    asset_type: AssetType = Form(),
    project_id: str | None = Form(default=None),
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    return to_api(await service.create_asset(db, user["_id"], file, brand_id, asset_type, project_id))


@router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: str, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)):
    return to_api(await get_owned_asset(db, user["_id"], asset_id))


@router.get("/{asset_id}/file")
async def download_asset(asset_id: str, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)):
    asset = await get_owned_asset(db, user["_id"], asset_id)
    path = file_path(asset["_id"])
    if not path.is_file():
        raise AppError(404, "FILE_MISSING", "저장된 파일을 찾을 수 없습니다.")
    return FileResponse(
        path,
        media_type=asset["mime_type"],
        filename=asset["file_name"],
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: str, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)):
    await service.delete_asset(db, user["_id"], asset_id)
