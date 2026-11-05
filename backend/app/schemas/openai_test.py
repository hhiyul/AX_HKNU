from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.schemas.common import RequestModel


class OpenAITestRequest(RequestModel):
    api_key: SecretStr = Field(min_length=1, max_length=512)

    @field_validator("api_key")
    @classmethod
    def check_format(cls, value: SecretStr) -> SecretStr:
        # 공백·한글·개행이 있으면 HTTP 헤더로 보낼 수 없다.
        if not all("!" <= char <= "~" for char in value.get_secret_value()):
            raise ValueError("API 키 형식이 올바르지 않습니다.")
        return value


class OpenAITestResult(BaseModel):
    ok: bool
    model_count: int | None = None
    reason: Literal["invalid_key", "timeout", "upstream_error"] | None = None
