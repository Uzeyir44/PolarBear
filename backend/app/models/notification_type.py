"""
notification_types — lookup table. Design doc section 2.17.

Open-ended by design — this is the list most likely to grow as
features ship (daily challenges, recycling rewards, etc. all add new
notification TYPES, not schema changes). Seed rows (initial migration
data, not modeled here): new_follower, competition_request,
competition_accepted, competition_won, competition_lost, qr_redeemed,
clothing_purchased.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import SmallInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .notification import Notification


class NotificationType(Base):
    __tablename__ = "notification_types"

    type_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    type_name: Mapped[str] = mapped_column(unique=True, nullable=False)

    notifications: Mapped[list["Notification"]] = relationship(back_populates="type")

    def __repr__(self) -> str:
        return f"<NotificationType {self.type_name!r}>"