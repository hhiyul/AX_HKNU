"""사용자가 다음 요청에 적용하는 참고 범위. 모델이 소유 범위를 지정하지 않는다."""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from app.schemas.common import RequestModel, Revision

KINDS = ["project", "brand", "plan", "record", "document", "task", "economics", "ledger", "memo", "file", "product", "contact"]
SourceKind = Literal["project", "brand", "plan", "record", "document", "task", "economics", "ledger", "memo", "file", "product", "contact"]
SourceKey = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_:.\-]+$")]


class ReferenceScope(RequestModel):
    kinds: list[SourceKind] = Field(default_factory=lambda: list(KINDS), max_length=12)
    excluded: list[SourceKey] = Field(default_factory=list, max_length=500)
    # 다른 대화 전체를 탐색하지 않고 사용자가 고른 메시지만 읽는다.
    message_ids: list[Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{24}$")]] = Field(default_factory=list, max_length=20)


class ScopeSave(RequestModel):
    scope: ReferenceScope
    revision: Revision
