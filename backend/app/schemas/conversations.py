"""AI 채팅 대화 목록 API의 요청·응답."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from app.schemas.common import RequestModel, Revision

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ConversationCreate(RequestModel):
    # 비우면 첫 질문으로 정해진다.
    title: Title | None = None


class ConversationUpdate(RequestModel):
    title: Title
    revision: Revision


class ConversationOut(BaseModel):
    id: str
    project_id: str
    # 첫 질문을 보내기 전의 새 대화는 null
    title: str | None = None
    # 마지막 메시지 앞부분
    preview: str = ""
    message_count: int = 0
    last_message_at: datetime | None = None
    status: Literal["active", "trash"]
    revision: int
    created_at: datetime
    updated_at: datetime
