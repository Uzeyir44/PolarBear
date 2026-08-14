"""
user_wardrobe — permanent ownership record. Design doc section 2.7.

UNIQUE(user_id, item_id) structurally prevents duplicate purchases —
no app-level "have they already bought this?" check needed before
insert; the database rejects the duplicate outright. Unequipping an
item never touches this table — "equipped" state lives entirely in
avatar_equipment, so there's exactly one place that can say what's
currently worn.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from .clothing_item import ClothingItem
    from .user import User


class UserWardrobe(Base):
    __tablename__ = "user_wardrobe"
    __table_args__ = (
        UniqueConstraint("user_id", "item_id", name="uq_user_wardrobe_no_duplicate_purchase"),
    )

    wardrobe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clothing_items.item_id", ondelete="RESTRICT"), nullable=False
    )
    purchased_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    user: Mapped["User"] = relationship(back_populates="wardrobe")
    item: Mapped["ClothingItem"] = relationship(back_populates="wardrobe_entries")

    def __repr__(self) -> str:
        return f"<UserWardrobe user_id={self.user_id} item_id={self.item_id}>"