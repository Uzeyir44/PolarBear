"""
avatars — one per user. Design doc section 2.3.

The 1:1 relationship is enforced by UNIQUE on user_id (not a second PK
column) — a user can have zero or one avatar row, never two.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .avatar_equipment import AvatarEquipment
    from .user import User


class Avatar(Base):
    __tablename__ = "avatars"

    avatar_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    user: Mapped["User"] = relationship(back_populates="avatar")
    equipment: Mapped[list["AvatarEquipment"]] = relationship(
        back_populates="avatar", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Avatar user_id={self.user_id}>"