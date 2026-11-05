from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, EmailStr, Field

from app.schemas.common import CreatorName, RequestModel

NormalizedEmail = Annotated[EmailStr, AfterValidator(str.lower)]


class SignupRequest(RequestModel):
    email: NormalizedEmail
    password: str = Field(min_length=8, max_length=128)
    creator_name: CreatorName


class LoginRequest(RequestModel):
    email: NormalizedEmail
    password: str = Field(max_length=128)


class ProfileUpdate(RequestModel):
    creator_name: CreatorName


class UserOut(BaseModel):
    id: str
    email: str
    creator_name: str
    created_at: datetime
    updated_at: datetime
