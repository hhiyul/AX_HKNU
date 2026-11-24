from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.common import LongText, RequestModel, ShortText, TextList

BrandType = Literal["new", "existing"]
DraftStatus = Literal["draft", "completed"]


class BrandCreate(RequestModel):
    # 이름과 소개만으로 브랜드를 만들 수 있다. 종류를 보내지 않으면 이미 운영 중인 브랜드로 본다.
    brand_type: BrandType = "existing"
    name: ShortText = None
    description: LongText = None
    target_customer: LongText = None
    keywords: TextList = []
    colors: TextList = []


class BrandUpdate(RequestModel):
    # 보낸 필드만 저장한다. brand_type, keywords, colors, status는 null을 받지 않는다.
    brand_type: BrandType = None
    name: ShortText = None
    description: LongText = None
    target_customer: LongText = None
    keywords: TextList = None
    colors: TextList = None
    logo_asset_id: str | None = None
    status: DraftStatus = None


class BrandOut(BaseModel):
    id: str
    user_id: str
    brand_type: BrandType
    name: str | None = None
    description: str | None = None
    target_customer: str | None = None
    keywords: list[str]
    colors: list[str]
    logo_asset_id: str | None = None
    status: DraftStatus
    created_at: datetime
    updated_at: datetime
