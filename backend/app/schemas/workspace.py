"""프로젝트 작업실: 글(기획 항목·AI 정리 내용·산출물), 경제성, AI 채팅, 개요."""
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.common import Amount, RequestModel, Revision
from app.schemas.projects import IdList

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
# 내용은 다듬지 않고 받은 그대로 저장한다. 종류별 길이 제한은 services/items.py의 RULES에 있다.
Content = Annotated[str, StringConstraints(max_length=50000)]

# ---------- 글 ----------

# plan 상품기획 6개 항목, record AI와 함께 정리한 내용, document 산출물
ItemKind = Literal["plan", "record", "document"]


class ItemSave(RequestModel):
    # content와 title은 보내지 않으면 그대로 둔다. 기획 항목은 내용을 비워 보내면 미작성으로 돌아간다.
    content: Content = None
    title: Title = None
    # 가격결정(plan/ck_05)에만 넣을 수 있는 판매가격. 보내지 않으면 그대로 둔다.
    amount: Amount = None
    # 기획 항목을 비워 저장할 때 "건너뜀"으로 표시할지
    skipped: bool = False
    # 산출물 편집기의 임시 저장. 저장된 문서는 그대로 두고 draft만 바꾼다.
    as_draft: bool = False
    revision: Revision


class ItemCreate(RequestModel):
    # message_id의 답변에 문서 초안이 있으면 title과 content를 생략할 수 있다.
    title: Title = None
    content: Content = None
    # 이 글을 저장한 AI 답변. 같은 답변을 다시 검수하면 기존 정리 내용을 고친다.
    message_id: str = None


class ItemDraft(BaseModel):
    title: str | None = None
    content: str
    updated_at: datetime


class ItemOut(BaseModel):
    kind: ItemKind
    key: str
    title: str | None = None
    # 기획 항목: UNSET 미작성, SKIPPED 건너뜀, WRITTEN 작성됨. 나머지 글은 늘 WRITTEN.
    state: Literal["UNSET", "SKIPPED", "WRITTEN"]
    content: str | None = None
    amount: int | None = None
    message_id: str | None = None
    # 산출물 편집기에서 아직 저장하지 않은 변경 내용
    draft: ItemDraft | None = None
    revision: int
    updated_at: datetime | None = None


# ---------- 경제성 ----------


class Costs(RequestModel):
    # 상품 1개 기준. null은 "아직 모름"이다. 0원과 구분하며 계산에서 0으로 채우지 않는다.
    material: Amount | None = None
    labor: Amount | None = None
    packaging: Amount | None = None
    # 판매자 부담 배송비
    shipping: Amount | None = None
    other: Amount | None = None
    # 판매 수수료(%)
    fee_rate: Annotated[float, Field(ge=0, le=100)] | None = None
    # 비교해 볼 판매가. 여기에 넣어도 확정 가격은 바뀌지 않는다.
    price: Amount | None = None


class EconomicsSave(RequestModel):
    cost: Costs
    revision: Revision


class RevisionOnly(RequestModel):
    revision: Revision


class PriceConfirm(RequestModel):
    # keep 기존 판매가 유지, change 입력한 판매가로 변경
    mode: Literal["keep", "change"]
    revision: Revision


class Calculation(BaseModel):
    price: int
    # 개당 비용 합
    cost: int
    # 판매 수수료
    fee: int
    # 한 개 판매 후 남는 금액
    net: int
    # 변경 전 기준보다 개당 얼마나 더 남는지. 기준이 없으면 null.
    delta: int | None = None


class EconomicsOut(BaseModel):
    project_id: str
    cost: dict[str, Any]
    # 변경 전 비교 기준. 저장한 적이 없으면 null.
    base: dict[str, Any] | None = None
    # 상품기획의 가격결정에 확정한 가격
    confirmed_price: int | None = None
    # 계산할 수 없으면(모르는 값이 있으면) null
    before: Calculation | None = None
    keep: Calculation | None = None
    change: Calculation | None = None
    # 입력한 판매가와 그 앞뒤 금액의 계산 결과
    candidates: list[Calculation] = []
    revision: int
    updated_at: datetime | None = None


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
    # AI가 만든 문서 초안 {title, content}
    doc: dict[str, Any] | None = None
    # 이 답변에서 저장한 정리 내용과 산출물. 저장하지 않았으면 null.
    record_key: str | None = None
    doc_key: str | None = None
    created_at: datetime


# ---------- 개요 · 맥락 · 요청 결과 ----------


class OverviewOut(BaseModel):
    project: dict[str, Any]
    brand: dict[str, Any] | None = None
    plan: list[dict[str, Any]]
    counts: dict[str, int]
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
