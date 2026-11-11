"""프로젝트 작업실의 나머지 영역: 추가 기록, 경제성, 기획 산출물, AI 채팅, AI 제안, 개요."""
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.schemas.common import RequestModel, Revision
from app.schemas.plan import Amount, Content, ItemKey, PlanItemOut, SaveKind

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
IdList = Annotated[list[str], Field(max_length=50)]

# ---------- 추가 기록 (F08) ----------

EntryState = Literal["DRAFT", "DECIDED"]


class EntryCreate(RequestModel):
    title: Title
    content: Content
    state: EntryState = "DRAFT"
    revision: Revision


class EntryUpdate(RequestModel):
    # 보낸 필드만 바꾼다.
    title: Title = None
    content: Content = None
    state: EntryState = None
    revision: Revision


class SimilarQuery(RequestModel):
    title: Annotated[str, StringConstraints(max_length=200)] = ""
    content: Content


class EntryOut(BaseModel):
    entry_id: str
    title: str
    content: str
    state: EntryState
    source: str
    source_ref: str | None = None
    created_at: datetime
    updated_at: datetime


class RecordOut(BaseModel):
    project_id: str
    entries: list[EntryOut]
    revision: int
    updated_at: datetime | None = None


class SimilarOut(BaseModel):
    score: float
    entry: EntryOut


# ---------- 경제성 (F05) ----------


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


class ScenarioAdopt(RequestModel):
    # 가격·원가(ck_05) 항목의 현재 revision
    revision: Revision


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
    item: PlanItemOut


# ---------- 기획 산출물 (F06) ----------

DocType = Literal["PLAN", "INTRO", "DETAIL_PAGE", "MARKETING", "PREP"]
# 문서를 만들 때 참고한 자료의 종류
Basis = Literal["DECIDED", "DRAFT", "PROPOSAL", "MANUAL"]
DocContent = Annotated[str, StringConstraints(max_length=50000)]


class DocumentCreate(RequestModel):
    doc_type: DocType
    title: Title
    content: DocContent = ""
    basis: Annotated[list[Basis], Field(max_length=4)] = ["MANUAL"]


class DocumentUpdate(RequestModel):
    title: Title = None
    content: DocContent = None
    revision: Revision


class RevisionOnly(RequestModel):
    revision: Revision


class DocumentOut(BaseModel):
    id: str
    project_id: str
    doc_type: DocType
    title: str
    content: str
    state: Literal["DRAFT", "FINAL"]
    basis: list[Basis]
    # 초안이나 AI 제안을 참고해 만든 문서인지
    uses_unconfirmed: bool
    # 문서를 만든 뒤 확정본이 바뀐 기획 항목. 비어 있지 않으면 최신화가 필요하다.
    stale_items: list[ItemKey]
    version: int
    revision: int
    created_at: datetime
    updated_at: datetime


class DocumentVersionOut(BaseModel):
    id: str
    document_id: str
    version: int
    title: str
    content: str
    finalized_at: datetime


# ---------- AI 채팅 (F03) ----------


class ChatUpdate(RequestModel):
    search_enabled: bool = None
    # 채팅이 참고하는 파일(내 파일 id)과 다른 프로젝트(읽기 전용 참고)
    file_refs: IdList = None
    chip_refs: IdList = None
    revision: Revision


class ChatOut(BaseModel):
    id: str
    project_id: str
    search_enabled: bool
    file_refs: list[str]
    chip_refs: list[str]
    message_count: int
    revision: int
    created_at: datetime
    updated_at: datetime


class MessageCreate(RequestModel):
    content: Annotated[str, StringConstraints(min_length=1, max_length=20000)]
    asset_ids: IdList = []


class MessageOut(BaseModel):
    id: str
    chat_id: str
    ordinal: int
    role: Literal["USER", "ASSISTANT"]
    content: str
    asset_ids: list[str]
    completion: Literal["COMPLETE", "PARTIAL", "UNKNOWN"]
    created_at: datetime


# ---------- AI 제안 (F01) ----------

ProposalTarget = Literal["ck_01", "ck_02", "ck_03", "ck_04", "ck_05", "ck_06", "record"]
ProposalState = Literal["PENDING", "HELD", "APPLIED", "REJECTED"]


class ProposalCreate(RequestModel):
    target: ProposalTarget
    content: Content
    message_id: str | None = None


class ProposalApply(RequestModel):
    # 사용자가 고르고 고친 최종 내용. 제안 원문과 달라도 된다.
    content: Content
    kind: SaveKind = "DRAFT"
    amount: Amount | None = None
    # 추가 기록으로 반영할 때의 제목
    title: Title = None
    # 반영 대상(기획 항목 또는 추가 기록)의 현재 revision
    revision: Revision


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
    record_entries: int
    economics: dict[str, Any]
    chat_messages: int
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
