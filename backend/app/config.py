from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_details: str = ""
    jwt_secret: str = ""
    cookie_secure: bool = False
    upload_dir: str = "/data/uploads"


@lru_cache
def get_settings() -> Settings:
    return Settings()
