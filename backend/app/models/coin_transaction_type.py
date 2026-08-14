"""
coin_transaction_types — lookup table. Design doc section 2.10.

A lookup table, not a Python enum, because this list is the fastest-
growing one in the schema — recycling rewards, admin adjustments,
refunds, and anything from "Future Features" all show up here as new
ROWS, never a schema change. Seed rows (initial migration data, not
modeled here): qr_redemption, competition_reward, clothing_purchase,
vote_cast, refund, admin_adjustment.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import SmallInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .enums import TransactionDirection

if TYPE_CHECKING:
    from .coin_transaction import CoinTransaction


class CoinTransactionType(Base):
    __tablename__ = "coin_transaction_types"

    type_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    type_name: Mapped[str] = mapped_column(unique=True, nullable=False)
    direction: Mapped[TransactionDirection] = mapped_column(
        SQLEnum(TransactionDirection, name="transaction_direction", native_enum=True),
        nullable=False,
    )

    transactions: Mapped[list["CoinTransaction"]] = relationship(back_populates="type")

    def __repr__(self) -> str:
        return f"<CoinTransactionType {self.type_name!r} ({self.direction})>"