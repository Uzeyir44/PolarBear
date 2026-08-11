"""
clothing_categories — lookup table. Design doc section 2.5.

Maps a shop category (e.g. "Sneakers", "Sunglasses") to the avatar
slot it equips into. This indirection is what lets `clothing_items`
avoid a direct slot column — the slot is inherited from the category.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import SmallInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .enums import AvatarSlot

if TYPE_CHECKING:
    from .clothing_item import ClothingItem


class ClothingCategory(Base):
    __tablename__ = "clothing_categories"

    category_id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True
    )
    category_name: Mapped[str] = mapped_column(unique=True, nullable=False)
    slot: Mapped[AvatarSlot] = mapped_column(
        SQLEnum(AvatarSlot, name="avatar_slot", native_enum=True), nullable=False
    )

    items: Mapped[list["ClothingItem"]] = relationship(back_populates="category")

    def __repr__(self) -> str:
        return f"<ClothingCategory {self.category_name!r} ({self.slot})>"