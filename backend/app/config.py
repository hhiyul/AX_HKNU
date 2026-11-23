from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_details: str = ""
    jwt_secret: str = ""
    cookie_secure: bool = False
    upload_dir: str = "/data/uploads"
    # AI 채팅. demo는 실제 호출 없는 모의 응답이고, openai로 바꾸면 비용이 든다.
    ai_provider: Literal["demo", "openai"] = "demo"
    openai_api_key: str = ""
    openai_model: str = "gpt-6-luna"


@lru_cache
def get_settings() -> Settings:
    return Settings()
