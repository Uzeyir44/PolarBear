"""
competitions — live and finished competitions only. Design doc section 2.15.

`end_time` is a real Postgres GENERATED column: start_time +
duration_minutes, computed by the database, not settable by any
query. `challenger_id`/`opponent_id`/`duration_minutes` are copied
from the accepted `competition_requests` row — deliberate
denormalization, justified because a request's terms are immutable
history once accepted.

TRIGGER: "one active competition per user" cannot be a plain
constraint — a user can appear in either the challenger_id or
opponent_id column across different rows, and Postgres can't
declaratively index "this value doesn't appear as either column, in
any row, with status=active." The trigger below rejects the
INSERT/UPDATE if either party already has another row with an
'active' status. This is the one rule in the whole schema that isn't
self-enforcing by table structure alone — it's enforced here, at the
database level, rather than only in application code, so it holds
even for writes that bypass the app (admin tools, migrations, etc.).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Computed, DDL, DateTime, ForeignKey, SmallInteger, event, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from .competition_request import CompetitionRequest
    from .competition_status import CompetitionStatus
    from .user import User
    from .vote import Vote


class Competition(Base):
    __tablename__ = "competitions"
    __table_args__ = (
        CheckConstraint("challenger_id <> opponent_id", name="ck_competitions_no_self_challenge"),
        CheckConstraint(
            "winner_id IS NULL OR winner_id IN (challenger_id, opponent_id)",
            name="ck_competitions_winner_is_participant",
        ),
    )

    competition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("competition_requests.request_id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    challenger_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    opponent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    status_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("competition_status.status_id"), nullable=False
    )
    prize_pool: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    total_votes: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    winner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=True
    )
    duration_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[datetime] = mapped_column(nullable=False)
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        Computed("start_time + (duration_minutes * interval '1 minute')", persisted=True),
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    request: Mapped["CompetitionRequest"] = relationship(back_populates="competition")
    challenger: Mapped["User"] = relationship(
        foreign_keys=[challenger_id], back_populates="competitions_as_challenger"
    )
    opponent: Mapped["User"] = relationship(
        foreign_keys=[opponent_id], back_populates="competitions_as_opponent"
    )
    winner: Mapped["User | None"] = relationship(
        foreign_keys=[winner_id], back_populates="competitions_won"
    )
    status: Mapped["CompetitionStatus"] = relationship(back_populates="competitions")
    votes: Mapped[list["Vote"]] = relationship(back_populates="competition", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Competition {self.challenger_id} vs {self.opponent_id}>"


# --- Trigger: one active competition per user -----------------------------
# Looks up the 'active' status_id by name so the trigger doesn't hardcode
# a specific row id from seed data.

_enforce_one_active_competition_fn = DDL("""
CREATE OR REPLACE FUNCTION enforce_one_active_competition_per_user()
RETURNS TRIGGER AS $$
DECLARE
    v_active_status_id SMALLINT;
    v_conflict_count INTEGER;
BEGIN
    SELECT status_id INTO v_active_status_id
    FROM competition_status WHERE status_name = 'active';

    IF NEW.status_id = v_active_status_id THEN
        SELECT COUNT(*) INTO v_conflict_count
        FROM competitions
        WHERE status_id = v_active_status_id
          AND competition_id <> NEW.competition_id
          AND (challenger_id IN (NEW.challenger_id, NEW.opponent_id)
               OR opponent_id IN (NEW.challenger_id, NEW.opponent_id));

        IF v_conflict_count > 0 THEN
            RAISE EXCEPTION
                'User % or % already has an active competition',
                NEW.challenger_id, NEW.opponent_id
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")

_enforce_one_active_competition_trigger = DDL("""
CREATE TRIGGER trg_enforce_one_active_competition_per_user
BEFORE INSERT OR UPDATE ON competitions
FOR EACH ROW
EXECUTE FUNCTION enforce_one_active_competition_per_user();
""")

event.listen(Competition.__table__, "after_create", _enforce_one_active_competition_fn)
event.listen(Competition.__table__, "after_create", _enforce_one_active_competition_trigger)