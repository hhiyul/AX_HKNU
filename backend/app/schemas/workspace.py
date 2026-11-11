"""프로젝트 작업실: 글(기획 항목·추가 기록·산출물), 경제성, AI 채팅, AI 제안, 개요."""
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StrictInt, StringConstraints, field_validator, model_validator

from app.schemas.common import RequestModel, Revision
from app.schemas.projects import IdList

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Amount = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
# 내용은 다듬지 않고 받은 그대로 저장한다. 종류별 길이 제한은 services/items.py의 RULES에 있다.
Content = Annotated[str, StringConstraints(max_length=50000)]

# ---------- 글 ----------

# plan 상품기획 6개 항목, record 추가 기록, document 기획 산출물
ItemKind = Literal["plan", "record", "document"]
# 기획 항목의 키. AI 파이프라인 명세의 섹션 이름과 맞춘다.
PlanKey = Literal["ck_01", "ck_02", "ck_03", "ck_04", "ck_05", "ck_06"]
# 초안 저장과 확정은 다른 동작이다.
SaveState = Literal["DRAFT", "DECIDED"]
DocType = Literal["PLAN", "INTRO", "DETAIL_PAGE", "MARKETING", "PREP"]
# 문서를 만들 때 참고한 자료의 종류
Basis = Literal["DECIDED", "DRAFT", "PROPOSAL", "MANUAL"]


class ItemSave(RequestModel):
    state: SaveState
    content: Content
    # 기록과 문서의 제목. 보내지 않으면 그대로 둔다.
    title: Title = None
    # 가격·원가(plan/ck_05)에만 넣을 수 있는 기획용 판매가격
    amount: Amount | None = None
    revision: Revision


class ItemCreate(RequestModel):
    title: Title
    content: Content = ""
    state: SaveState = "DRAFT"
    # 문서(document)를 만들 때만 쓴다.
    doc_type: DocType = None
    basis: Annotated[list[Basis], Field(max_length=4)] = ["MANUAL"]


class BatchItem(ItemSave):
    key: PlanKey


class ItemBatch(RequestModel):
    items: Annotated[list[BatchItem], Field(min_length=1, max_length=6)]

    @field_validator("items")
    @classmethod
    def unique_keys(cls, items: list[BatchItem]) -> list[BatchItem]:
        if len({item.key for item in items}) != len(items):
            raise ValueError("같은 항목을 두 번 보낼 수 없습니다.")
        return items


class SimilarQuery(RequestModel):
    title: Annotated[str, StringConstraints(max_length=200)] = ""
    content: Content


class RevisionOnly(RequestModel):
    revision: Revision


class ItemPart(BaseModel):
    content: str
    amount: int | None = None
    # USER 직접 입력, AI 제안 반영, ECONOMICS 경제성에서 넘어옴
    source: str = "USER"
    source_ref: str | None = None
    version: int | None = None
    updated_at: datetime | None = None
    decided_at: datetime | None = None


class ItemOut(BaseModel):
    kind: ItemKind
    key: str
    title: str | None = None
    # 초안이 있으면 DRAFT, 확정본만 있으면 DECIDED, 둘 다 없으면 UNSET
    state: Literal["UNSET", "DRAFT", "DECIDED"]
    # 지금 화면에 보여 줄 내용 (초안이 있으면 초안, 없으면 확정본)
    content: str | None = None
    amount: int | None = None
    draft: ItemPart | None = None
    decided: ItemPart | None = None
    version: int
    revision: int
    updated_at: datetime | None = None
    # 아래는 문서(document)에만 있다.
    doc_type: DocType | None = None
    basis: list[Basis] | None = None
    # 초안이나 AI 제안을 참고해 만든 문서인지
    uses_unconfirmed: bool | None = None
    # 문서를 만든 뒤 확정본이 바뀐 기획 항목. 비어 있지 않으면 최신화가 필요하다.
    stale_items: list[PlanKey] | None = None


class BatchResult(BaseModel):
    key: PlanKey
    status: Literal["APPLIED", "REJECTED"]
    code: str | None = None
    message: str | None = None
    item: ItemOut | None = None


class ItemVersionOut(BaseModel):
    id: str
    kind: ItemKind
    key: str
    version: int
    title: str | None = None
    content: str
    amount: int | None = None
    source: str
    decided_at: datetime


class SimilarOut(BaseModel):
    score: float
    item: ItemOut


# ---------- 경제성 ----------


class CostItem(RequestModel):
    label: Label
    category: Literal["material", "labor", "packaging", "shipping", "fee", "other"] = "other"
    # WON은 판매 단위당 금액(원), PERCENT는 판매가에 대한 비율(%)
    unit: Literal["WON", "PERCENT"] = "WON"
    # null은 "아직 모름"이다. 0원과 구분하며 계산에서 0으로 채우지 않는다.
    amount: Annotated[float, Field(ge=0, le=10**12)] | None = None
    # 사용자가 아는 값인지, 임시로 넣은 가정인지
    basis: Literal["USER", "ASSUMPTION"] = "USER"
    # 가정은 사용자가 승인해야 계산에 쓴다.
    approved: bool = False

    @model_validator(mode="after")
    def check_amount(self):
        if self.amount is not None:
            if self.unit == "WON" and self.amount != int(self.amount):
                raise ValueError("원 단위 금액은 정수여야 합니다.")
            if self.unit == "PERCENT" and self.amount > 100:
                raise ValueError("비율은 100 이하여야 합니다.")
        return self


class CostSheetSave(RequestModel):
    items: Annotated[list[CostItem], Field(max_length=50)]
    revision: Revision


class CostSheetOut(BaseModel):
    project_id: str
    items: list[dict[str, Any]]
    revision: int
    updated_at: datetime | None = None


class ScenarioCreate(RequestModel):
    price: Amount
    label: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""


class ScenarioOut(BaseModel):
    id: str
    project_id: str
    label: str
    price: int
    unit_cost: int
    fee: int
    profit: int
    margin_rate: float | None = None
    # 모르는 비용이나 승인하지 않은 가정이 빠져 있으면 false. 이때 이익은 확정 계산이 아니다.
    complete: bool
    uses_assumptions: bool
    excluded: list[dict[str, str]]
    sheet_revision: int
    adopted_at: datetime | None = None
    created_at: datetime


class ScenarioAdoptOut(BaseModel):
    scenario: ScenarioOut
    item: ItemOut


# ---------- AI 채팅 ----------


class MessageCreate(RequestModel):
    content: Annotated[str, StringConstraints(min_length=1, max_length=20000)]
    asset_ids: IdList = []


class MessageOut(BaseModel):
    id: str
    project_id: str
    ordinal: int
    role: Literal["USER", "ASSISTANT"]
    content: str
    asset_ids: list[str]
    completion: Literal["COMPLETE", "PARTIAL", "UNKNOWN"]
    created_at: datetime


# ---------- AI 제안 ----------

# 기획 항목 하나를 바꾸는 제안이거나, 추가 기록으로 남기자는 제안
ProposalTarget = Literal["ck_01", "ck_02", "ck_03", "ck_04", "ck_05", "ck_06", "record"]
ProposalState = Literal["PENDING", "HELD", "APPLIED", "REJECTED"]


class ProposalCreate(RequestModel):
    target: ProposalTarget
    content: Content
    message_id: str | None = None


class ProposalApply(RequestModel):
    # 사용자가 고르고 고친 최종 내용. 제안 원문과 달라도 된다.
    content: Content
    state: SaveState = "DRAFT"
    amount: Amount | None = None
    # 추가 기록으로 반영할 때의 제목
    title: Title = None
    # 반영할 기획 항목의 현재 revision. 추가 기록은 새로 만들어지므로 쓰지 않는다.
    revision: Revision = 0


class ProposalOut(BaseModel):
    id: str
    project_id: str
    target: ProposalTarget
    content: str
    message_id: str | None = None
    state: ProposalState
    # 제안을 만들 때의 대상 revision과 지금의 revision. 다르면 그 사이에 대상이 바뀐 것이다.
    base_revision: int
    current_revision: int
    target_changed: bool
    applied_content: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ---------- 개요 · 맥락 · 요청 결과 ----------


class OverviewOut(BaseModel):
    project: dict[str, Any]
    brand: dict[str, Any] | None = None
    plan: list[dict[str, Any]]
    proposals: dict[str, int]
    documents: dict[str, Any]
    records: int
    economics: dict[str, Any]
    active_jobs: int


class ProjectionOut(BaseModel):
    project_id: str
    # 포함된 섹션들의 버전을 묶은 값. 같으면 그 사이에 바뀐 것이 없다.
    bundle_revision: str
    sections: dict[str, Any]


class RequestOut(BaseModel):
    key: str
    state: Literal["PROCESSING", "APPLIED", "NOT_APPLIED"]
    error: dict[str, Any] | None = None
    result: Any = None
    created_at: datetime
