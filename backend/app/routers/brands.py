from fastapi import APIRouter, Depends, status
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.deps import Page, current_user
from app.schemas.brands import BrandCreate, BrandOut, BrandUpdate
from app.schemas.common import to_api
from app.services import brands as service
from app.services.store import get_owned_brand

router = APIRouter(prefix="/brands", tags=["brands"])


@router.get("", response_model=list[BrandOut])
async def list_brands(
    page: Page = Depends(), user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)
):
    return to_api(await service.list_brands(db, user["_id"], page))


@router.post("", response_model=BrandOut, status_code=status.HTTP_201_CREATED)
async def create_brand(
    body: BrandCreate, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)
):
    return to_api(await service.create_brand(db, user["_id"], body))


@router.get("/{brand_id}", response_model=BrandOut)
async def get_brand(brand_id: str, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)):
    return to_api(await get_owned_brand(db, user["_id"], brand_id))


@router.patch("/{brand_id}", response_model=BrandOut)
async def update_brand(
    brand_id: str,
    body: BrandUpdate,
    user: dict = Depends(current_user),
    db: AsyncDatabase = Depends(get_db),
):
    return to_api(await service.update_brand(db, user["_id"], brand_id, body))


@router.delete("/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brand(brand_id: str, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)):
    await service.delete_brand(db, user["_id"], brand_id)
