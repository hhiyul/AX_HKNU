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
    # OpenAI 호환 주소. OpenAI는 https://api.openai.com/v1, BAZE 게이트웨이는 https://api.baze.co/v1/gateway
    openai_base_url: str = "https://api.openai.com/v1"
    # 비우면 OpenAI는 none, 게이트웨이는 low. 게이트웨이는 low·medium·high만 받는다.
    openai_reasoning_effort: Literal["", "none", "low", "medium", "high"] = ""
    # GD-048: 명시한 참고 파일의 TXT·MD·DOCX 원문을 읽는다. 운영 설정으로 끌 때는 미독 상태를 알린다.
    ai_read_attachments: bool = True
    # 채팅에 붙인 사진(PNG·JPEG·WebP)을 AI가 보게 한다. 글 파일 읽기(위)와 따로 켜고 끈다.
    ai_read_images: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
