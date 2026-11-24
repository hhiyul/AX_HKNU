"""AI 채팅 실행 API의 요청·응답."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from app.schemas.common import RequestModel
from app.schemas.projects import IdList


class RunCreate(RequestModel):
    # 앞뒤 공백은 지운다. 공백만 있으면 422(실제 호출 비용이 드는 빈 요청을 막는다).
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]
    # 웹 검색에 써도 되는 공개 검색어. 프로젝트의 검색 허용이 켜져 있을 때만 쓴다.
    public_search_terms: Annotated[str, StringConstraints(max_length=500)] = ""
    # auto: 대화(문서 요청이면 문서), chat: 대화만, document: 문서 초안
    mode: Literal["auto", "chat", "document"] = "auto"
    # 문서를 만들 때 이전 대화를 함께 넣을지. 비우면 넣는다.
    include_session: bool | None = None
    # 이번 메시지에 붙인 내 파일(POST /assets로 올린 것). TXT·MD·DOCX는 AI가 읽는다.
    asset_ids: IdList = []
    # 문서 모드에서 참고할 기존 산출물의 key와, 저장하지 않은 편집 중인 글(있으면 저장된 내용 대신 쓴다)
    reference_document: str | None = None
    reference_body: Annotated[str, StringConstraints(max_length=50000)] | None = None
    # 메시지를 보낼 대화(GET /projects/{id}/conversations). 비우면 가장 최근 대화, 없으면 새 대화
    conversation_id: str | None = None


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
