"""
votes — design doc section 2.16.

UNIQUE(competition_id, voter_id) does double duty: one vote per user
per competition, AND makes votes effectively immutable — there's no
UPDATE path exposed by the app, so a vote row is insert-only by
convention. CHECK blocks self-votes.

TRIGGER: "voted_for_user_id must be a participant in that specific
competition" needs a cross-table lookup (checking against
competitions.challenger_id/opponent_id), which a plain CHECK
constraint can't do — CHECK constraints can only see the row being
inserted, not other tables. Enforced below at the database level for
the same reason as the active-competition trigger in competition.py:
it should hold even for writes that bypass the application.

Each vote insert should, in the same application transaction, also:
(1) insert a debit row into coin_transactions, and (2) increment
competitions.prize_pool and total_votes by 1. Those two steps aren't
triggers here deliberately — they're plain writes the service layer
makes, not integrity rules a bad actor could otherwise violate.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DDL, ForeignKey, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from .competition import Competition
    from .user import User


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("competition_id", "voter_id", name="uq_votes_one_per_user_per_competition"),
        CheckConstraint("voter_id <> voted_for_user_id", name="ck_votes_no_self_vote"),
    )

    vote_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    competition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("competitions.competition_id", ondelete="CASCADE"), nullable=False
    )
    voter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    voted_for_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    competition: Mapped["Competition"] = relationship(back_populates="votes")
    voter: Mapped["User"] = relationship(foreign_keys=[voter_id], back_populates="votes_cast")
    voted_for: Mapped["User"] = relationship(
        foreign_keys=[voted_for_user_id], back_populates="votes_received"
    )

    def __repr__(self) -> str:
        return f"<Vote competition_id={self.competition_id} voter_id={self.voter_id}>"


# --- Trigger: vote target must be a participant in that competition -------

_enforce_vote_target_fn = DDL("""
CREATE OR REPLACE FUNCTION enforce_vote_target_is_participant()
RETURNS TRIGGER AS $$
DECLARE
    v_challenger UUID;
    v_opponent UUID;
BEGIN
    SELECT challenger_id, opponent_id INTO v_challenger, v_opponent
    FROM competitions WHERE competition_id = NEW.competition_id;

    IF NEW.voted_for_user_id NOT IN (v_challenger, v_opponent) THEN
        RAISE EXCEPTION
            'voted_for_user_id % is not a participant in competition %',
            NEW.voted_for_user_id, NEW.competition_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")

_enforce_vote_target_trigger = DDL("""
CREATE TRIGGER trg_enforce_vote_target_is_participant
BEFORE INSERT ON votes
FOR EACH ROW
EXECUTE FUNCTION enforce_vote_target_is_participant();
""")

event.listen(Vote.__table__, "after_create", _enforce_vote_target_fn)
event.listen(Vote.__table__, "after_create", _enforce_vote_target_trigger)