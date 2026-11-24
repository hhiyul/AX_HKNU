"""할 일(일정) API의 요청·응답."""
import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from app.schemas.common import RequestModel, Revision

TaskTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
TaskNote = Annotated[str, StringConstraints(max_length=1000)]


class TaskCreate(RequestModel):
    title: TaskTitle
    # 날짜는 필수(YYYY-MM-DD). AI 후보의 날짜가 없으면 사용자가 골라야 한다.
    date: dt.date
    note: TaskNote = ""
    # 문서 항목에서 만든 일정이면 원래 문서·항목, AI 후보에서 만들었으면 후보 id
    source_doc_id: str | None = None
    source_section_id: str | None = None
    source_proposal_id: str | None = None


class TaskUpdate(RequestModel):
    title: TaskTitle | None = None
    date: dt.date | None = None
    note: TaskNote | None = None
    done: bool | None = None
    revision: Revision


class TaskSource(BaseModel):
    doc_id: str
    doc_title: str | None = None
    # active, trash, missing(문서를 완전히 지움)
    doc_status: str
    section_id: str | None = None
    section_title: str | None = None


class TaskOut(BaseModel):
    id: str
    project_id: str
    title: str
    date: str
    note: str = ""
    done: bool
    source: TaskSource | None = None
    source_proposal_id: str | None = None
    status: Literal["active", "trash"]
    # 같은 AI 후보로 이미 등록돼 있던 일정을 돌려줬으면 true
    existing: bool = False
    revision: int
    created_at: dt.datetime
    updated_at: dt.datetime
