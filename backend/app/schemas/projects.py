from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.common import LongText, RequestModel, Revision

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Step = Annotated[str, Field(min_length=1, max_length=50)]
IdList = Annotated[list[str], Field(max_length=50)]


# 프로젝트는 이름만 있으면 시작할 수 있다 (UX Flow v0.3 F00). 브랜드 연결은 선택이다.
# 기획 내용은 여기가 아니라 /projects/{id}/items 에 저장한다.
class ProjectCreate(RequestModel):
    name: Name
    brand_id: str | None = None
    description: LongText = None
    # 마지막으로 작업하던 화면. 재방문 때 이어서 열 위치로 쓴다 (F11).
    current_step: Step = "overview"


class ProjectUpdate(RequestModel):
    # 보낸 필드만 저장한다. brand_id에 null을 보내면 연결을 푼다. 나머지는 null을 받지 않는다.
    revision: Revision
    name: Name = None
    brand_id: str | None = None
    description: LongText = None
    current_step: Step = None
    # AI 채팅 설정: 웹 검색 사용, 참고할 내 파일, 읽기 전용으로 참고할 다른 프로젝트
    search_enabled: bool = None
    file_refs: IdList = None
    chip_refs: IdList = None


class ProjectOut(BaseModel):
    id: str
    user_id: str
    brand_id: str | None = None
    name: str | None = None
    description: str | None = None
    current_step: str
    search_enabled: bool = False
    file_refs: list[str] = []
    chip_refs: list[str] = []
    message_count: int = 0
    revision: int = 1
    created_at: datetime
    updated_at: datetime
