"""
Pydantic schemas for the admin user-management module.

UserAdminRead is deliberately ADMIN-wider than the public schemas: it
adds email, coin_balance, winning_streak and updated_at. It never
includes password_hash, auth-provider data, JWT/session details or any
other authentication material — anything not listed here is stripped by
FastAPI's response_model filtering.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserAdminRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    username: str
    email: str
    profile_picture_url: str | None = None
    biography: str | None = None
    coin_balance: int
    winning_streak: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserAdminStatusUpdate(BaseModel):
    """Body of PATCH /admin/users/{user_id}/status.

    The only user field an administrator may change: activation state.
    There is deliberately nothing else here, so a client cannot smuggle
    in coin adjustments, passwords, or role edits through this endpoint.
    """

    is_active: bool


class UserAdminList(BaseModel):
    """Paginated response of GET /admin/users.

    total is the count of users matching the current search/filter
    combination; items/limit/offset describe the returned page.
    """

    items: list[UserAdminRead]
    total: int
    limit: int
    offset: int