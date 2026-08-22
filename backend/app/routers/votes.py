"""
Votes — Phase 6, Part 4B-1. Basic vote casting.

  POST /competitions/{competition_id}/votes    cast a vote (201)

FOR THIS STAGE VOTING IS FREE: no coin is deducted, no coin_transactions
row is written, and users.coin_balance is never touched. The 1-vote = 1-coin
cost arrives in Part 4B-2.

Rules enforced (all against the authenticated JWT identity, never the
frontend):
  - the competition must exist (404) and be ACTIVE (409 otherwise);
  - the voter must NOT be a participant (challenger OR opponent) — a
    participant can never vote in their own competition;
  - voted_for_user_id must be one of the two participants;
  - one vote per user per competition — UNIQUE(competition_id, voter_id).

The pseudo-flow (one SQLAlchemy transaction):

    load competition                  (404 / not-active / self / target checks)
    INSERT votes
    atomic UPDATE competitions
        SET total_votes = total_votes + 1
    COMMIT  — or ROLLBACK (e.g. duplicate-vote IntegrityError -> 409)

Atomicity: the vote row and the total_votes increment commit (or roll back)
together, so "vote exists but count unchanged" and "count up but no vote" are
both impossible. Concurrency: the INSERT is subject to the UNIQUE constraint,
and the increment is a single atomic `total_votes = total_votes + 1` UPDATE
(read-modify-write is never done in Python), so two simultaneous votes from
the same voter yield exactly one row — the loser's transaction rolls back and
returns 409 — while two different voters each increment the count exactly once.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import Competition, CompetitionStatus, User, Vote
from app.schemas.vote import VoteCreate, VoteRead

router = APIRouter(prefix="/competitions", tags=["votes"])

_UNIQUE_VOTE_CONSTRAINT = "uq_votes_one_per_user_per_competition"


@router.post(
    "/{competition_id}/votes",
    response_model=VoteRead,
    status_code=status.HTTP_201_CREATED,
)
def cast_vote(
    competition_id: uuid.UUID,
    payload: VoteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VoteRead:
    competition = db.get(Competition, competition_id)
    if competition is None:
        raise _competition_not_found()

    if competition.status_id != _active_status_id(db):
        raise _not_active()

    # Self-voting: the voter's identity comes ONLY from the JWT. A participant
    # cannot vote in their own competition, for either participant.
    if (
        competition.challenger_id == current_user.user_id
        or competition.opponent_id == current_user.user_id
    ):
        raise _self_vote()

    # Target must be one of the two participants.
    if payload.voted_for_user_id not in (
        competition.challenger_id,
        competition.opponent_id,
    ):
        raise _invalid_target()

    db.add(
        Vote(
            competition_id=competition_id,
            voter_id=current_user.user_id,
            voted_for_user_id=payload.voted_for_user_id,
        )
    )
    # Atomic increment — an UPDATE that adds one, never a Python read-modify-write.
    db.execute(
        update(Competition)
        .where(Competition.competition_id == competition_id)
        .values(total_votes=Competition.total_votes + 1)
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == _UNIQUE_VOTE_CONSTRAINT:
            raise _already_voted() from exc
        # The vote-target trigger (from vote.py's DDL) would also surface here
        # if it were installed on a fresh database — treat it as an invalid
        # target rather than a surprising 500.
        raise _invalid_target() from exc

    db.refresh(competition)
    vote = db.execute(
        select(Vote).where(
            Vote.competition_id == competition_id,
            Vote.voter_id == current_user.user_id,
        ).limit(1)
    ).scalars().first()

    return VoteRead(
        vote_id=vote.vote_id,
        competition_id=competition_id,
        voter_id=current_user.user_id,
        voted_for_user_id=vote.voted_for_user_id,
        created_at=vote.created_at,
        total_votes=competition.total_votes,
    )


def _active_status_id(db: Session) -> int:
    status_id = db.scalar(
        select(CompetitionStatus.status_id).where(CompetitionStatus.status_name == "active")
    )
    if status_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Active competition status is not configured",
        )
    return status_id


def _competition_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Competition not found",
    )


def _not_active() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Competition is no longer active",
    )


def _self_vote() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="You cannot vote in your own competition",
    )


def _invalid_target() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="You can only vote for a participant of this competition",
    )


def _already_voted() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="You have already voted in this competition",
    )