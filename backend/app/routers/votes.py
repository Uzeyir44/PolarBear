"""
Votes — Phase 6, Part 4B. Vote casting with a 1-coin cost.

  POST /competitions/{competition_id}/votes    cast a vote (201)

A successful vote costs exactly 1 coin (a fixed business rule — no
configurable price) and grows the competition's prize_pool by exactly 1 coin
in the same transaction. The whole operation is ONE transaction:

    BEGIN
      validations                 (competition exists/active, not a
                                   participant, target is a participant)
      INSERT votes                (UNIQUE(competition_id, voter_id) gate —
                                   a duplicate rolls back BEFORE any coin
                                   moves)
      UPDATE users
          SET coin_balance = coin_balance - 1
          WHERE user_id = :me AND coin_balance >= 1
          RETURNING coin_balance  (atomic, guarded: never negative)
      INSERT coin_transactions    (type vote_cast/DEBIT, amount -1,
                                   balance_after, vote_id, competition_id)
      UPDATE competitions
          SET total_votes = total_votes + 1,
              prize_pool  = prize_pool + 1
          WHERE competition_id = :id AND status_id = <active>
    COMMIT  — or ROLLBACK

Atomicity: vote + balance + ledger + vote count + prize pool commit (or roll
back) together, so "vote exists but coins not deducted", "coins deducted but
no vote", "ledger row without a vote", or "prize pool grew without a vote"
are all impossible.

Concurrency: the balance is decremented with a single atomic conditional
UPDATE (`coin_balance - 1 ... WHERE coin_balance >= 1`, RETURNING the new
value) — the same pattern qr.py uses to credit coins. Under READ COMMITTED
Postgres re-evaluates the WHERE after acquiring the row lock, so a user's
last coin cannot be spent twice: of two simultaneous vote transactions, only
one wins the guarded decrement; the other sees `coin_balance >= 1` fail,
rolls back its vote entirely, and returns the insufficient-coins error.
Duplicate votes (same voter, same competition) are gated by the UNIQUE
constraint on the FIRST write (the vote INSERT), before any balance change.
The final competition UPDATE is additionally guarded on `status_id = active`:
if a /complete request finalizes the competition between this vote's status
read and its final write, the UPDATE affects 0 rows and the whole vote rolls
back (the competition can never accept & count a vote its winner calculation
did not see).

Validation ordering: everything that can reject the request (self-vote,
invalid target, completed competition, duplicate) happens BEFORE the balance
is touched. Insufficient balance is the last validation and changes nothing
on failure.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import CoinTransaction, CoinTransactionType, Competition, CompetitionStatus, User, Vote
from app.schemas.vote import VoteCreate, VoteRead

router = APIRouter(prefix="/competitions", tags=["votes"])

_UNIQUE_VOTE_CONSTRAINT = "uq_votes_one_per_user_per_competition"
_VOTE_COST = 1
_VOTE_TYPE_NAME = "vote_cast"


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

    active_status_id = _active_status_id(db)
    if competition.status_id != active_status_id:
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

    # --- One atomic business operation: vote + coin balance + ledger + count ---
    vote = Vote(
        competition_id=competition_id,
        voter_id=current_user.user_id,
        voted_for_user_id=payload.voted_for_user_id,
    )
    db.add(vote)
    try:
        # Flush the vote FIRST: the UNIQUE(competition_id, voter_id) gate fires
        # here, so a duplicate is rejected and rolled back BEFORE any coin moves.
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == _UNIQUE_VOTE_CONSTRAINT:
            raise _already_voted() from exc
        # The vote-target trigger (from vote.py's DDL) would also surface here
        # if it were installed on a fresh database — treat it as an invalid
        # target rather than a surprising 500.
        raise _invalid_target() from exc

    # Atomic guarded deduction (never negative, never a Python read-modify-write).
    new_balance = db.execute(
        update(User)
        .where(User.user_id == current_user.user_id, User.coin_balance >= _VOTE_COST)
        .values(coin_balance=User.coin_balance - _VOTE_COST)
        .returning(User.coin_balance)
    ).scalar_one_or_none()
    if new_balance is None:
        # Lost the coin race (or genuinely broke): roll back the vote entirely.
        db.rollback()
        raise _insufficient_coins()

    # Resolve the seeded vote_cast DEBIT lookup row by name (never by seed id).
    vote_type_id = db.scalar(
        select(CoinTransactionType.type_id).where(
            CoinTransactionType.type_name == _VOTE_TYPE_NAME
        )
    )
    if vote_type_id is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Vote transaction type is not configured",
        )

    # Signed amounts: credit = positive, debit = negative (vote_cast is DEBIT).
    db.add(
        CoinTransaction(
            user_id=current_user.user_id,
            type_id=vote_type_id,
            amount=-_VOTE_COST,
            balance_after=new_balance,
            vote_id=vote.vote_id,
            competition_id=competition_id,
        )
    )
    # Atomic increments — a single UPDATE that adds one to BOTH the vote count
    # and the prize pool, never a Python read-modify-write. A successful vote
    # costs the voter 1 coin and grows the competition's prize_pool by 1, in
    # the same transaction as the vote + balance + ledger writes.
    # status_id = active guard: a competition finalized by /complete in between
    # our read and this write (i.e. a vote racing completion) gets 0 rows here,
    # and the whole vote rolls back — a completed competition can never accept
    # a late vote that the winner calculation did not see.
    result = db.execute(
        update(Competition)
        .where(
            Competition.competition_id == competition_id,
            Competition.status_id == active_status_id,
        )
        .values(
            total_votes=Competition.total_votes + 1,
            prize_pool=Competition.prize_pool + 1,
        )
    )
    if result.rowcount == 0:
        db.rollback()
        raise _not_active()

    db.commit()
    db.refresh(competition)
    db.refresh(vote)
    return VoteRead(
        vote_id=vote.vote_id,
        competition_id=competition_id,
        voter_id=current_user.user_id,
        voted_for_user_id=vote.voted_for_user_id,
        created_at=vote.created_at,
        total_votes=competition.total_votes,
        balance_after=new_balance,
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


def _insufficient_coins() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Not enough coins to vote",
    )