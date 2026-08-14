"""
auth_providers — external identity linking. Design doc section 2.2.

Empty in practice today (no OAuth flow exists yet), but modeled up
front so adding Google/Apple login later is an INSERT into this table,
not a migration on `users`. The UNIQUE(provider, provider_user_id)
constraint stops the same external account from being linked to two
different local users.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from .enums import AuthProviderType

if TYPE_CHECKING:
    from .user import User


class AuthProvider(Base):
    __tablename__ = "auth_providers"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_user_id", name="uq_auth_providers_provider_identity"
        ),
    )

    auth_provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[AuthProviderType] = mapped_column(
        SQLEnum(AuthProviderType, name="auth_provider_type", native_enum=True), nullable=False
    )
    provider_user_id: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    user: Mapped["User"] = relationship(back_populates="auth_providers")

    def __repr__(self) -> str:
        return f"<AuthProvider {self.provider}:{self.provider_user_id!r}>"