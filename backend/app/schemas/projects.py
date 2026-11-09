from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StrictInt

from app.schemas.brands import DraftStatus
from app.schemas.common import LongText, RequestModel, ShortText, TextList

Price = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
Step = Annotated[str, Field(min_length=1, max_length=50)]


# TODO(IA v0.1): 상품기획 6개 항목(상품 선정, 고객 선정, 구매 가치, 상품 구성, 가격·원가, 제작 가능성)과
#   항목별 상태(미작성/초안/검토 중/확정)를 저장할 필드가 아직 없다. DB 설계 확정 후 추가한다.
# TODO(IA v0.1): IA는 항목을 자유로운 순서로 작업한다. current_step은 순서형 진행 단계라서,
#   항목별 상태가 생기면 "마지막으로 보던 화면" 용도로 바꿀지 정해야 한다.
# TODO(IA v0.1): expected_price는 임시 값이다. 판매가 결정은 상품기획 '가격·원가 결정',
#   비용 입력과 시나리오는 경제성 영역으로 옮겨질 수 있다.
class ProjectCreate(RequestModel):
    brand_id: str
    name: ShortText = None
    description: LongText = None
    target_customer: LongText = None
    features: TextList = []
    expected_price: Price | None = None
    current_step: Step = "basic"


class ProjectUpdate(RequestModel):
    # 보낸 필드만 저장한다. features, current_step, status는 null을 받지 않는다.
    name: ShortText = None
    description: LongText = None
    target_customer: LongText = None
    features: TextList = None
    expected_price: Price | None = None
    current_step: Step = None
    status: DraftStatus = None


class ProjectOut(BaseModel):
    id: str
    brand_id: str
    name: str | None = None
    description: str | None = None
    target_customer: str | None = None
    features: list[str]
    expected_price: int | None = None
    current_step: str
    status: DraftStatus
    created_at: datetime
    updated_at: datetime
