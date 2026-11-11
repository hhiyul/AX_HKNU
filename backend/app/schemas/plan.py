from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictInt, StringConstraints, field_validator

from app.schemas.common import RequestModel, Revision

# 상품기획 6개 항목. 키는 AI 파이프라인 명세의 섹션 이름(ck_01~ck_06)과 맞춘다.
ItemKey = Literal["ck_01", "ck_02", "ck_03", "ck_04", "ck_05", "ck_06"]
# 저장 종류. 초안 저장과 확정은 다른 동작이다.
SaveKind = Literal["DRAFT", "DECIDED"]
ItemState = Literal["UNSET", "DRAFT", "DECIDED"]
Amount = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
# 내용은 다듬지 않고 받은 그대로 저장한다.
Content = Annotated[str, StringConstraints(min_length=1, max_length=5000)]


class PlanSave(RequestModel):
    kind: SaveKind
    content: Content
    # 가격·원가(ck_05)에만 넣을 수 있는 기획용 판매가격
    amount: Amount | None = None
    revision: Revision

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("내용이 비어 있습니다.")
        return value


class PlanBatchItem(PlanSave):
    item_key: ItemKey


class PlanBatch(RequestModel):
    items: Annotated[list[PlanBatchItem], Field(min_length=1, max_length=6)]

    @field_validator("items")
    @classmethod
    def unique_keys(cls, items: list[PlanBatchItem]) -> list[PlanBatchItem]:
        if len({item.item_key for item in items}) != len(items):
            raise ValueError("같은 항목을 두 번 보낼 수 없습니다.")
        return items


class PlanPart(BaseModel):
    content: str
    amount: int | None = None
    # USER(직접 작성), AI(제안 반영), ECONOMICS(경제성에서 고른 가격)
    source: str
    source_ref: str | None = None
    updated_at: datetime | None = None
    version: int | None = None
    decided_at: datetime | None = None


class PlanItemOut(BaseModel):
    item_key: ItemKey
    label: str
    # 초안이 있으면 DRAFT, 확정본만 있으면 DECIDED, 둘 다 없으면 UNSET
    state: ItemState
    # 지금 작업 기준이 되는 내용: 초안이 있으면 초안, 없으면 확정본
    content: str | None = None
    amount: int | None = None
    draft: PlanPart | None = None
    decided: PlanPart | None = None
    revision: int
    updated_at: datetime | None = None


class PlanBatchResult(BaseModel):
    item_key: ItemKey
    status: Literal["APPLIED", "REJECTED"]
    code: str | None = None
    message: str | None = None
    item: PlanItemOut | None = None


class PlanVersionOut(BaseModel):
    id: str
    item_key: ItemKey
    version: int
    content: str
    amount: int | None = None
    source: str
    source_ref: str | None = None
    decided_at: datetime
