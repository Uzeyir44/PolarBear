"""
competition_requests — the pre-competition negotiation. Design doc
section 2.13.

Separated from `competitions` because the two have different
lifecycles: a request is pending/accepted/declined/cancelled and never
has a prize pool or a timer; a competition is active/completed and
always has both.

Multiple pending requests are allowed — there's simply no uniqueness
constraint blocking it. When one is accepted, the app (in one
transaction) must: (1) insert the corresponding `competitions` row,
(2) set this row's status to 'accepted', and (3) bulk-UPDATE every
other 'pending' request involving either party to 'cancelled'. That
third step only touches this table, so it's a plain UPDATE — no
trigger needed here (contrast with `competitions`, which does need
one; see competition.py).
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

from app.core.database import Base
from .enums import CompetitionRequestStatus

if TYPE_CHECKING:
    from .competition import Competition
    from .user import User


class CompetitionRequest(Base):
    __tablename__ = "competition_requests"
    __table_args__ = (
        CheckConstraint("challenger_id <> opponent_id", name="ck_competition_requests_no_self_challenge"),
        CheckConstraint(
            "duration_minutes IN (30, 60, 360, 1440)",
            name="ck_competition_requests_duration_allowed_values",
        ),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    challenger_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    opponent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    duration_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[CompetitionRequestStatus] = mapped_column(
        SQLEnum(CompetitionRequestStatus, name="competition_request_status", native_enum=True),
        nullable=False,
        server_default=CompetitionRequestStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(nullable=True)

    challenger: Mapped["User"] = relationship(
        foreign_keys=[challenger_id], back_populates="sent_competition_requests"
    )
    opponent: Mapped["User"] = relationship(
        foreign_keys=[opponent_id], back_populates="received_competition_requests"
    )
    competition: Mapped["Competition | None"] = relationship(
        back_populates="request", uselist=False
    )

    def __repr__(self) -> str:
        return f"<CompetitionRequest {self.challenger_id} -> {self.opponent_id} ({self.status})>"