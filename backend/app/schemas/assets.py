from datetime import datetime
from typing import Literal

from pydantic import BaseModel

AssetType = Literal["logo", "banner", "product_image", "document"]


class AssetOut(BaseModel):
    id: str
    brand_id: str
    project_id: str | None = None
    ai_job_id: str | None = None
    asset_type: AssetType
    source: Literal["upload", "ai"]
    file_url: str
    file_name: str
    mime_type: str
    created_at: datetime
    updated_at: datetime
