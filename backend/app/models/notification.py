"""
notifications — design doc section 2.18.

`metadata` is the one deliberate deviation from strict normalization
in the whole schema: notification payloads vary per type (a
competition notification needs a competition_id, a follow
notification just needs actor_user_id), and new notification types
keep appearing per the roadmap. Rather than adding a new nullable FK
column here for every future notification type, type-specific
reference IDs live in this JSONB blob. `actor_user_id` stays a real
FK because it's common to nearly every notification type and benefits
from actual referential integrity — it's the one reference worth
enforcing structurally; the rest aren't common enough to justify a
column each.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, SmallInteger, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from .notification_type import NotificationType
    from .user import User


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # Backs the "unread feed" query — by far the most common read
        # against this table: WHERE user_id = ? AND is_read = false
        # ORDER BY created_at DESC.
        Index("ix_notifications_user_id_is_read_created_at", "user_id", "is_read", "created_at"),
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    type_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("notification_types.type_id"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
    )
    # Python attribute is `payload`, not `metadata` — `metadata` is a
    # reserved name on SQLAlchemy declarative models (Base.metadata is
    # the MetaData object). The actual DB column is still named
    # `metadata`, matching the design doc, via the explicit name= below.
    payload: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    is_read: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    user: Mapped["User"] = relationship(
        foreign_keys=[user_id], back_populates="notifications_received"
    )
    actor: Mapped["User | None"] = relationship(
        foreign_keys=[actor_user_id], back_populates="notifications_triggered"
    )
    type: Mapped["NotificationType"] = relationship(back_populates="notifications")

    def __repr__(self) -> str:
        return f"<Notification user_id={self.user_id} type_id={self.type_id}>"