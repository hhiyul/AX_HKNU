from typing import Annotated, Any

from bson import ObjectId
from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints


class RequestModel(BaseModel):
    # 정의하지 않은 필드는 받지 않는다.
    model_config = ConfigDict(extra="forbid")


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


CreatorName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
# 작성 중에는 비워둘 수 있는 글자 필드. 공백만 있으면 비운 것으로 본다.
ShortText = Annotated[str | None, StringConstraints(max_length=200), AfterValidator(_blank_to_none)]
LongText = Annotated[str | None, StringConstraints(max_length=5000), AfterValidator(_blank_to_none)]
TextList = list[Annotated[str, StringConstraints(max_length=100)]]


def to_api(value: Any) -> Any:
    """Mongo 문서를 응답 형태로 바꾼다. _id는 id로, ObjectId는 문자열로."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, list):
        return [to_api(item) for item in value]
    if isinstance(value, dict):
        return {("id" if key == "_id" else key): to_api(item) for key, item in value.items()}
    return value
