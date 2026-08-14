"""
follows — self-referential many-to-many on users. Design doc section 2.12.

Table is named `follows` to match the design doc — flag it if you
actually want it renamed to `user_follows` everywhere (doc + model +
future migration), otherwise I'm keeping doc and code in sync as-is.

There's no follow_id: the composite PK (follower_id, followee_id) IS
the "no duplicate follow" constraint — structurally impossible to
follow the same person twice. A CHECK constraint blocks self-follows.
followee_id gets its own index because the PK's leading column only
gives you "who do I follow" for free; "who follows me" needs the
second index explicitly.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from .user import User


class Follow(Base):
    __tablename__ = "follows"
    __table_args__ = (
        CheckConstraint("follower_id <> followee_id", name="ck_follows_no_self_follow"),
        Index("ix_follows_followee_id", "followee_id"),
    )

    follower_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    followee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    follower: Mapped["User"] = relationship(
        foreign_keys=[follower_id], back_populates="following"
    )
    followee: Mapped["User"] = relationship(
        foreign_keys=[followee_id], back_populates="followers"
    )

    def __repr__(self) -> str:
        return f"<Follow {self.follower_id} -> {self.followee_id}>"