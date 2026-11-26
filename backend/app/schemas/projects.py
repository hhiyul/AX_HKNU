from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.schemas.common import Amount, LongText, PlanKey, RequestModel, Revision

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Step = Annotated[str, Field(min_length=1, max_length=50)]
IdList = Annotated[list[str], Field(max_length=50)]
# AI가 함께 읽는 참고 프로젝트는 5개까지
MAX_CHIP_REFS = 5
ChipRefs = Annotated[list[str], Field(max_length=MAX_CHIP_REFS)]
# 진행 단계. 사용자가 직접 바꾸며 작성률에 따라 자동으로 바뀌지 않는다.
Stage = Literal["planning", "prototype", "ready", "selling"]
# active 진행 중, archived 보관함, trash 휴지통
Status = Literal["active", "archived", "trash"]


class PlanAnswer(RequestModel):
    key: PlanKey
    # 비워 두면 건너뛴 항목으로 남는다.
    content: Annotated[str, StringConstraints(max_length=5000)] = ""
    # 가격결정(ck_05)에만 넣을 수 있는 판매가격
    amount: Amount | None = None


# 프로젝트는 이름만 있으면 시작할 수 있다. 브랜드 연결은 선택이다.
class ProjectCreate(RequestModel):
    name: Name
    brand_id: str | None = None
    description: LongText = None
    # 마지막으로 작업하던 화면. 재방문 때 이어서 열 위치로 쓴다.
    current_step: Step = "overview"
    stage: Stage = "planning"
    # 새 프로젝트 마법사의 6개 문항 답변. 보내지 않으면 6개 모두 미작성으로 시작한다.
    plan: Annotated[list[PlanAnswer], Field(max_length=6)] | None = None

    @field_validator("plan")
    @classmethod
    def unique_keys(cls, plan: list[PlanAnswer] | None) -> list[PlanAnswer] | None:
        if plan and len({answer.key for answer in plan}) != len(plan):
            raise ValueError("같은 항목을 두 번 보낼 수 없습니다.")
        return plan


class ProjectUpdate(RequestModel):
    # 보낸 필드만 저장한다. brand_id, description, note는 null을 보내면 비운다. 나머지는 null을 받지 않는다.
    revision: Revision
    name: Name = None
    brand_id: str | None = None
    description: LongText = None
    current_step: Step = None
    stage: Stage = None
    # 보관(archived), 휴지통으로 이동(trash), 복원(active)
    status: Status = None
    # 나만의 메모장
    note: LongText = None
    # AI 채팅 설정: 웹 검색 사용, 참고할 내 파일, 읽기 전용으로 참고할 다른 프로젝트
    search_enabled: bool = None
    file_refs: IdList = None
    chip_refs: ChipRefs = None


class ProjectOut(BaseModel):
    id: str
    user_id: str
    brand_id: str | None = None
    name: str | None = None
    description: str | None = None
    current_step: str
    stage: Stage = "planning"
    status: Status
    archived_at: datetime | None = None
    deleted_at: datetime | None = None
    note: str | None = None
    # 다음에 해야 할 일. AI 파이프라인이 채운다.
    next_action: str | None = None
    # 상품기획 6개 항목 중 작성한 개수
    plan_filled: int = 0
    # 최근 작업 20건. 최신이 먼저다.
    recent: list[dict[str, Any]] = []
    search_enabled: bool = False
    file_refs: list[str] = []
    chip_refs: list[str] = []
    message_count: int = 0
    # 지금 답변을 만드는 AI 실행 id. 화면이 새로고침 뒤에도 진행 중인 답변을 이어 받는 데 쓴다.
    active_run: str | None = None
    revision: int = 1
    created_at: datetime
    updated_at: datetime
