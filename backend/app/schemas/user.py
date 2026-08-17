"""
Pydantic schemas for user data.

Separate input schemas (what the client sends us) from output schemas
(what we return). The output schema defines exactly which fields are
exposed — anything not listed here, such as password_hash, is stripped
from the response by FastAPI's response_model filtering.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRegister(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=30,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="3-30 characters: letters, digits, or underscore",
    )
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=128,
        description="At least 8 characters",
    )


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    username: str
    email: str
    is_active: bool
    created_at: datetime
