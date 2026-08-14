"""
avatar_equipment — what's currently equipped, per slot. Design doc section 2.4.

The composite PK (avatar_id, slot) is the entire enforcement mechanism
for "only one item per slot" — it's structurally impossible to insert
two rows for the same avatar+slot, so there's no app-level check
needed. Changing equipment is an UPDATE (or upsert) on the existing
row for that slot, never an insert of a second row. item_id is
nullable so a slot can be legitimately empty.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from .enums import AvatarSlot

if TYPE_CHECKING:
    from .avatar import Avatar
    from .clothing_item import ClothingItem


class AvatarEquipment(Base):
    __tablename__ = "avatar_equipment"

    avatar_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("avatars.avatar_id", ondelete="CASCADE"), primary_key=True
    )
    slot: Mapped[AvatarSlot] = mapped_column(
        SQLEnum(AvatarSlot, name="avatar_slot", native_enum=True), primary_key=True
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clothing_items.item_id", ondelete="RESTRICT"), nullable=True
    )
    equipped_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    avatar: Mapped["Avatar"] = relationship(back_populates="equipment")
    item: Mapped["ClothingItem | None"] = relationship(back_populates="equipped_in")

    def __repr__(self) -> str:
        return f"<AvatarEquipment avatar_id={self.avatar_id} slot={self.slot}>"