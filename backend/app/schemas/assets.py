from datetime import datetime
from typing import Literal

from pydantic import BaseModel

AssetType = Literal["logo", "banner", "product_image", "document"]


class AssetOut(BaseModel):
    id: str
    # 브랜드와 프로젝트 중 적어도 하나에 속한다.
    brand_id: str | None = None
    project_id: str | None = None
    ai_job_id: str | None = None
    asset_type: AssetType
    source: Literal["upload", "ai"]
    file_url: str
    file_name: str
    mime_type: str
    created_at: datetime
    updated_at: datetime
