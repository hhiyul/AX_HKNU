"""정산 수입·지출 내역 API의 요청·응답."""
import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.common import RequestModel, Revision

EntryType = Literal["income", "expense"]
EntryTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
EntryNote = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
# 원 단위, 1원부터 1조 원까지
EntryAmount = Annotated[int, Field(ge=1, le=1_000_000_000_000)]


class EntryCreate(RequestModel):
    type: EntryType
    title: EntryTitle
    amount: EntryAmount
    date: dt.date
    note: EntryNote = ""


class EntryUpdate(RequestModel):
    # 보낸 필드만 바꾼다.
    type: EntryType | None = None
    title: EntryTitle | None = None
    amount: EntryAmount | None = None
    date: dt.date | None = None
    note: EntryNote | None = None
    revision: Revision


class EntryOut(BaseModel):
    id: str
    project_id: str
    type: EntryType
    title: str
    amount: int
    # YYYY-MM-DD
    date: str
    note: str = ""
    status: Literal["active", "trash"]
    revision: int
    created_at: dt.datetime
    updated_at: dt.datetime


class LedgerOut(BaseModel):
    # 휴지통에 없는 내역의 합계(원)
    income: int
    expense: int
    entries: list[EntryOut]
