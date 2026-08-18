"""
Pydantic schemas for user data.

Separate input schemas (what the client sends us) from output schemas
(what we return). The output schema defines exactly which fields are
exposed — anything not listed here, such as password_hash, is stripped
from the response by FastAPI's response_model filtering.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


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


class UserUpdate(BaseModel):
    """Body of PATCH /users/me.

    Every field is optional: the client may send only the fields it wants
    to change. A field the client omits stays untouched (see the endpoint,
    which applies model_dump(exclude_unset=True) so omitted fields are
    never written). Sending null for biography or profile_picture_url is a
    valid way to *clear* them; username can never be null because the DB
    column is NOT NULL.
    """

    username: str | None = Field(
        default=None,
        min_length=3,
        max_length=30,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="3-30 characters: letters, digits, or underscore",
    )
    biography: str | None = Field(
        default=None,
        max_length=500,
        description="Short bio shown on the profile",
    )
    profile_picture_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Public URL of the profile picture",
    )

    @field_validator("username")
    @classmethod
    def username_must_not_be_null(cls, value: str | None) -> str | None:
        # Field validators only run for values the client actually sent.
        # Omitting username skips this; sending {"username": null} triggers
        # it and produces a 422 instead of a DB NOT NULL violation.
        if value is None:
            raise ValueError("username may not be null")
        return value


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    username: str
    email: str
    biography: str | None = None
    profile_picture_url: str | None = None
    is_active: bool
    created_at: datetime
