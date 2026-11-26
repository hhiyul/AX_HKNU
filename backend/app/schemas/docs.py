"""항목형 전용 문서 API의 요청·응답."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.common import RequestModel, Revision
from app.schemas.projects import IdList
from app.schemas.ai_scope import ReferenceScope

TemplateId = Literal["funding", "business_plan"]
DocTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
SectionText = Annotated[str, StringConstraints(max_length=20000)]


class DocCreate(RequestModel):
    # 모르는 틀은 서비스가 422 UNKNOWN_TEMPLATE로 알린다.
    template_id: str
    title: DocTitle | None = None
    origin_message_id: str | None = None
    product_ids: IdList = []


class DocRename(RequestModel):
    title: DocTitle
    revision: Revision


class DraftSave(RequestModel):
    text: SectionText
    draft_revision: Revision


class SectionSave(RequestModel):
    text: SectionText
    # 보내지 않으면 자료 연결은 그대로 둔다.
    material_ids: IdList | None = None
    revision: Revision


class SectionReview(RequestModel):
    revision: Revision


class Progress(BaseModel):
    required_total: int
    required_reviewed: int
    optional_total: int
    optional_reviewed: int


class SectionOut(BaseModel):
    section_id: str
    title: str
    question: str
    guide: str
    required: bool
    # UNWRITTEN 미작성, WRITING 작성 중, REVIEWED 검토 완료
    state: Literal["UNWRITTEN", "WRITING", "REVIEWED"]
    text: str
    draft: str | None = None
    has_draft: bool
    draft_revision: int
    revision: int
    reviewed_revision: int | None = None
    material_ids: list[str] = []
    updated_at: datetime | None = None


class DocSummaryOut(BaseModel):
    id: str
    project_id: str
    title: str
    template_id: str
    template_version: int
    last_section_id: str | None = None
    last_section_title: str | None = None
    status: Literal["active", "trash"]
    deleted_at: datetime | None = None
    progress: Progress
    revision: int
    created_at: datetime
    updated_at: datetime


class DocOut(DocSummaryOut):
    sections: list[SectionOut]
    origin_message_id: str | None = None
    origin_conversation_id: str | None = None
    product_snapshots: list[dict] = []
    purpose_ids: list[str] = []


class PreviewOut(BaseModel):
    doc_id: str
    title: str
    text: str
    # 임시 입력은 제외하고 현재 저장본을 미리보기·내보내기한다.
    has_unsaved_drafts: bool


class Reference(RequestModel):
    # plan 기획 항목(ck_01~ck_06), section 같은 문서의 다른 항목, brand 연결 브랜드,
    # record AI와 함께 정리한 내용, message 대화 메시지, note 사용자가 적은 발췌
    type: Literal["plan", "section", "brand", "record", "message", "note", "file", "product", "contact"]
    id: str | None = None
    text: Annotated[str, StringConstraints(max_length=5000)] | None = None


class AssistRequest(RequestModel):
    # draft 초안 만들기, refine 현재 글 다듬기
    action: Literal["draft", "refine", "check"]
    request: Annotated[str, StringConstraints(max_length=2000)] = ""
    references: Annotated[list[Reference], Field(max_length=20)] | None = None


class AssistContextSave(RequestModel):
    references: Annotated[list[Reference], Field(max_length=20)]
    scope: ReferenceScope
    revision: Revision


class ApplyCandidate(RequestModel):
    message_id: str
    draft_revision: Revision
    # 후보를 만든 뒤 글이 바뀌었어도 적용하려면 true
    confirm: bool = False


class RunStarted(BaseModel):
    run_id: str


class ProposalRequest(RequestModel):
    request: Annotated[str, StringConstraints(max_length=2000)] = ""
    references: Annotated[list[Reference], Field(max_length=20)] | None = None
