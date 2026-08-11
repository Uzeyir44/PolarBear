"""
device_tokens — push notification device registry. Design doc section 2.19.

A user can have many devices — no uniqueness on user_id. push_token is
globally unique: a reinstall or re-registration should UPDATE the
existing row (matched by push_token) rather than insert a duplicate.
Sending a push to a user is: SELECT ... WHERE user_id = ? AND
is_active = true.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .enums import DevicePlatform

if TYPE_CHECKING:
    from .user import User


class DeviceToken(Base):
    __tablename__ = "device_tokens"
    __table_args__ = (
        Index("ix_device_tokens_user_id_is_active", "user_id", "is_active"),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[DevicePlatform] = mapped_column(
        SQLEnum(DevicePlatform, name="device_platform", native_enum=True), nullable=False
    )
    push_token: Mapped[str] = mapped_column(unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    # Set explicitly by the app on each check-in/app-open — NOT an
    # onupdate column, since "last seen" has specific semantics distinct
    # from "last row modification."
    last_seen_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    user: Mapped["User"] = relationship(back_populates="device_tokens")

    def __repr__(self) -> str:
        return f"<DeviceToken {self.platform}:{self.device_id}>"