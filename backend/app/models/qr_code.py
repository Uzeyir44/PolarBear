"""
qr_codes — one row per physical QR code printed. Design doc section 2.9.

The CHECK constraint keeps `status` and the redemption fields from
ever falling out of sync with each other at the row level. It does
NOT by itself guarantee "redeemed only once" under concurrent
requests — that needs the app to UPDATE with `WHERE status = 'active'`
in the same transaction that awards coins, so a second concurrent
redemption attempt affects zero rows instead of racing the first.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .enums import QRStatus

if TYPE_CHECKING:
    from .product import Product
    from .user import User


class QRCode(Base):
    __tablename__ = "qr_codes"
    __table_args__ = (
        CheckConstraint("coin_value > 0", name="ck_qr_codes_coin_value_positive"),
        CheckConstraint(
            "(status = 'redeemed') = (redeemed_by_user_id IS NOT NULL AND redeemed_at IS NOT NULL)",
            name="ck_qr_codes_redemption_fields_consistent",
        ),
    )

    qr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    code: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.product_id", ondelete="RESTRICT"), nullable=False
    )
    coin_value: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[QRStatus] = mapped_column(
        SQLEnum(QRStatus, name="qr_status", native_enum=True),
        nullable=False,
        server_default=QRStatus.ACTIVE.value,
    )
    redeemed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=True
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    product: Mapped["Product"] = relationship(back_populates="qr_codes")
    redeemed_by: Mapped["User | None"] = relationship(back_populates="qr_codes_redeemed")

    def __repr__(self) -> str:
        return f"<QRCode {self.code!r} status={self.status}>"