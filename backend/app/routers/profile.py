from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.db import get_db
from app.deps import current_user
from app.schemas.auth import ProfileUpdate, UserOut
from app.schemas.common import to_api

router = APIRouter(prefix="/profile", tags=["profile"])


@router.patch("", response_model=UserOut)
async def update_profile(
    body: ProfileUpdate, user: dict = Depends(current_user), db: AsyncDatabase = Depends(get_db)
):
    updated = await db.users.find_one_and_update(
        {"_id": user["_id"]},
        {"$set": {"creator_name": body.creator_name, "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    return to_api(updated)
