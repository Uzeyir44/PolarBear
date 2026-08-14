"""
coin_transactions — the ledger. Design doc section 2.11.

This is the source of truth for coins; `users.coin_balance` is only a
cache of it. `balance_after` is a point-in-time snapshot for audit —
it should never be recomputed after the fact, only written once at
insert time by whatever service layer also updates the user's cached
balance in the same DB transaction.

Reference columns are real FKs, not a polymorphic (reference_type,
reference_id) pair — Postgres can validate a real FK; it can't
validate a type-dependent one. vote_id and competition_id are plain
nullable UUID columns for now (no FK) because `votes` and
`competitions` don't exist yet — same extensibility pattern as
`clothing_items.collection_id`. They become real FKs once that batch
is built; flag it then so the migration adds the constraint.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .coin_transaction_type import CoinTransactionType
    from .qr_code import QRCode
    from .user import User
    from .user_wardrobe import UserWardrobe


class CoinTransaction(Base):
    __tablename__ = "coin_transactions"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="ck_coin_transactions_amount_nonzero"),
        Index("ix_coin_transactions_user_id_created_at", "user_id", "created_at"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    type_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("coin_transaction_types.type_id"), nullable=False
    )
    # Signed: positive = credit, negative = debit.
    amount: Mapped[int] = mapped_column(nullable=False)
    balance_after: Mapped[int] = mapped_column(nullable=False)

    qr_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("qr_codes.qr_id", ondelete="RESTRICT"), nullable=True
    )
    wardrobe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_wardrobe.wardrobe_id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Plain columns, no FK yet — see module docstring.
    vote_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    competition_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False, index=True
    )

    user: Mapped["User"] = relationship(back_populates="coin_transactions")
    type: Mapped["CoinTransactionType"] = relationship(back_populates="transactions")
    qr_code: Mapped["QRCode | None"] = relationship()
    wardrobe_entry: Mapped["UserWardrobe | None"] = relationship()

    def __repr__(self) -> str:
        return f"<CoinTransaction user_id={self.user_id} amount={self.amount}>"