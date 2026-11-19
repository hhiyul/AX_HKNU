"""AI 채팅 실행 API의 요청·응답."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from app.schemas.common import RequestModel


class RunCreate(RequestModel):
    text: Annotated[str, StringConstraints(min_length=1, max_length=20000)]


class RunStarted(BaseModel):
    run_id: str


class RunOut(BaseModel):
    id: str
    project_id: str
    status: Literal["running", "completed", "failed", "stopped", "unknown"]
    error_code: str | None = None
    # not_dispatched 안 보냄, unknown 결과 모름, responded 응답 받음
    delivery: str | None = None
    # 압축·생성 호출 수와 토큰 사용량
    usage: dict | None = None
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class StopOut(BaseModel):
    # stop_requested 또는 이미 끝난 실행의 상태
    status: str
