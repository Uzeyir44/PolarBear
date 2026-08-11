"""
clothing_items — the shop catalog. Design doc section 2.6.

collection_id is a plain nullable UUID column, NOT a ForeignKey yet —
`clothing_collections` doesn't exist as a table (it's a future
extension point per the design doc's "Future Feature Extension
Points" table, for seasonal/celebrity collections). When that table
is added, this column becomes a real FK via migration; until then it
just reserves the shape so `clothing_items` never needs restructuring.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, SmallInteger, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .enums import ClothingAvailability

if TYPE_CHECKING:
    from .avatar_equipment import AvatarEquipment
    from .clothing_category import ClothingCategory
    from .user_wardrobe import UserWardrobe


class ClothingItem(Base):
    __tablename__ = "clothing_items"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_clothing_items_price_non_negative"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    category_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("clothing_categories.category_id"), nullable=False
    )
    price: Mapped[int] = mapped_column(nullable=False)
    image_url: Mapped[str] = mapped_column(nullable=False)
    availability_status: Mapped[ClothingAvailability] = mapped_column(
        SQLEnum(ClothingAvailability, name="clothing_availability", native_enum=True),
        nullable=False,
        server_default=ClothingAvailability.AVAILABLE.value,
    )
    # Extensibility hook — see docstring above. No FK constraint yet.
    collection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    category: Mapped["ClothingCategory"] = relationship(back_populates="items")
    wardrobe_entries: Mapped[list["UserWardrobe"]] = relationship(back_populates="item")
    equipped_in: Mapped[list["AvatarEquipment"]] = relationship(back_populates="item")

    def __repr__(self) -> str:
        return f"<ClothingItem {self.name!r} ({self.price} coins)>"