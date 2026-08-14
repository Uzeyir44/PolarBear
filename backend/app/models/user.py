"""
users — core identity record. Design doc section 2.1.

IMPORTANT: coin_balance is a CACHE, not a source of truth. Once the
coin_transactions ledger exists (a later batch), nothing should ever
write to coin_balance directly except the transaction service layer
that also inserts the corresponding ledger row in the same DB
transaction. Treat it as read-only from anywhere else in the codebase.

"Profile" fields (username, bio, profile picture, etc.) live directly
on this table by design — see the discussion in chat: a separate
`profiles` table isn't warranted unless the field count explodes or
auth data needs access-control separation from profile data. Neither
applies here.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, text
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .auth_provider import AuthProvider
    from .avatar import Avatar
    from .coin_transaction import CoinTransaction
    from .device_token import DeviceToken
    from .follow import Follow
    from .qr_code import QRCode
    from .user_wardrobe import UserWardrobe


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("coin_balance >= 0", name="ck_users_coin_balance_non_negative"),
        CheckConstraint("winning_streak >= 0", name="ck_users_winning_streak_non_negative"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )

    # CITEXT requires `CREATE EXTENSION IF NOT EXISTS citext;` — see design
    # doc section 2.1 for why (case-insensitive uniqueness on username/email).
    username: Mapped[str] = mapped_column(CITEXT(), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(CITEXT(), unique=True, nullable=False, index=True)

    # Nullable because OAuth-only accounts (future Google/Apple login) have
    # no local password.
    password_hash: Mapped[str | None] = mapped_column(nullable=True)

    profile_picture_url: Mapped[str | None] = mapped_column(nullable=True)
    biography: Mapped[str | None] = mapped_column(nullable=True)

    coin_balance: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    winning_streak: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))

    # Soft delete flag — see design doc section 5 on why users are
    # deactivated, not hard-deleted (the coin ledger and competition
    # history both depend on the user row still existing).
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), onupdate=text("now()"), nullable=False
    )

    # Relationships populated as later batches add the referencing tables
    # (avatars, wardrobe, competitions, etc. aren't declared yet).
    auth_providers: Mapped[list["AuthProvider"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    device_tokens: Mapped[list["DeviceToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    avatar: Mapped["Avatar | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    wardrobe: Mapped[list["UserWardrobe"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    qr_codes_redeemed: Mapped[list["QRCode"]] = relationship(back_populates="redeemed_by")
    coin_transactions: Mapped[list["CoinTransaction"]] = relationship(back_populates="user")

    # Self-referential through Follow — two distinct relationships
    # because a user shows up in both the follower_id and followee_id
    # columns across different rows.
    following: Mapped[list["Follow"]] = relationship(
        foreign_keys="Follow.follower_id", back_populates="follower", cascade="all, delete-orphan"
    )
    followers: Mapped[list["Follow"]] = relationship(
        foreign_keys="Follow.followee_id", back_populates="followee", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User {self.username!r}>"