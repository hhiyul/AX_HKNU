from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StrictInt

from app.schemas.brands import DraftStatus
from app.schemas.common import LongText, RequestModel, ShortText, TextList

Price = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
Step = Annotated[str, Field(min_length=1, max_length=50)]


class ProjectCreate(RequestModel):
    brand_id: str
    name: ShortText = None
    description: LongText = None
    target_customer: LongText = None
    features: TextList = []
    expected_price: Price | None = None
    current_step: Step = "basic"


class ProjectUpdate(RequestModel):
    # 보낸 필드만 저장한다. features, current_step, status는 null을 받지 않는다.
    name: ShortText = None
    description: LongText = None
    target_customer: LongText = None
    features: TextList = None
    expected_price: Price | None = None
    current_step: Step = None
    status: DraftStatus = None


class ProjectOut(BaseModel):
    id: str
    brand_id: str
    name: str | None = None
    description: str | None = None
    target_customer: str | None = None
    features: list[str]
    expected_price: int | None = None
    current_step: str
    status: DraftStatus
    created_at: datetime
    updated_at: datetime
