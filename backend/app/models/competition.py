"""
competitions — live and finished competitions only. Design doc section 2.15.

`end_time` is a real Postgres GENERATED column: start_time +
duration_minutes, computed by the database, not settable by any
query. `challenger_id`/`opponent_id`/`duration_minutes` are copied
from the accepted `competition_requests` row — deliberate
denormalization, justified because a request's terms are immutable
history once accepted.

ACTIVE-COMPETITION LIMITS: a user may participate in at most 3
`active` competitions simultaneously, and a given unordered pair of
users may have at most 1 `active` competition at a time (historical
completed ones are fine). These cannot be plain constraints — a user
can appear in either the challenger_id or opponent_id column across
different rows. They are enforced race-safely at the application
layer (SELECT ... FOR UPDATE on both participant user rows before
accepting a request) and backed up by a BEFORE INSERT/UPDATE trigger
installed on table creation (never applied to the live DB).
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


# --- Trigger: active-competition business rules ----------------------------
# The app enforces these rules race-safely by taking SELECT ... FOR UPDATE row
# locks on BOTH participant user rows before it accepts a request (see
# routers/competition_requests.py) — the locks serialize concurrent
# competition-creation transactions that share a user, which the trigger's
# own COUNT queries cannot do. The trigger below is a backstop for any write
# that bypasses the app (admin tools, migrations, a fresh create_all) and is
# installed only on table creation via DDL events; it was never applied to the
# live database. Rules enforced (status_name = 'active' competitions only):
#   1. a user may be a participant in at most 3 active competitions;
#   2. the same unordered pair may have at most 1 active competition.

_enforce_competition_active_limits_fn = DDL("""
CREATE OR REPLACE FUNCTION enforce_competition_active_limits()
RETURNS TRIGGER AS $$
DECLARE
    v_active_status_id SMALLINT;
    v_participant RECORD;
    v_active_count INTEGER;
BEGIN
    SELECT status_id INTO v_active_status_id
    FROM competition_status WHERE status_name = 'active';

    -- Unknown status or a competition not entering 'active': nothing to enforce.
    IF v_active_status_id IS NULL OR NEW.status_id <> v_active_status_id THEN
        RETURN NEW;
    END IF;

    -- Rule 2: at most one active competition per unordered pair.
    IF EXISTS (
        SELECT 1 FROM competitions
        WHERE status_id = v_active_status_id
          AND competition_id <> NEW.competition_id
          AND ((challenger_id = NEW.challenger_id AND opponent_id = NEW.opponent_id)
            OR (challenger_id = NEW.opponent_id AND opponent_id = NEW.challenger_id))
    ) THEN
        RAISE EXCEPTION
            'Users % and % already have an active competition',
            NEW.challenger_id, NEW.opponent_id
            USING ERRCODE = '23514';
    END IF;

    -- Rule 1: each participant may be in at most 3 active competitions.
    FOR v_participant IN
        SELECT unnest(ARRAY[NEW.challenger_id, NEW.opponent_id]) AS user_id
    LOOP
        SELECT COUNT(*) INTO v_active_count
        FROM competitions
        WHERE status_id = v_active_status_id
          AND competition_id <> NEW.competition_id
          AND (challenger_id = v_participant.user_id OR opponent_id = v_participant.user_id);

        IF v_active_count >= 3 THEN
            RAISE EXCEPTION
                'User % already has the maximum of 3 active competitions',
                v_participant.user_id
                USING ERRCODE = '23514';
        END IF;
    END LOOP;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")

_enforce_competition_active_limits_trigger = DDL("""
CREATE TRIGGER trg_enforce_competition_active_limits
BEFORE INSERT OR UPDATE ON competitions
FOR EACH ROW
EXECUTE FUNCTION enforce_competition_active_limits();
""")

event.listen(Competition.__table__, "after_create", _enforce_competition_active_limits_fn)
event.listen(Competition.__table__, "after_create", _enforce_competition_active_limits_trigger)