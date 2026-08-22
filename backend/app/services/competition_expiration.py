"""
Competition expiration & completion — the single shared implementation used by
BOTH the manual endpoint (POST /competitions/{id}/complete) and the automatic
background sweeper that finalizes competitions once their end_time has passed.

Lifecycle rules (from the competition model / SQLAlchemy schema):
  * a competition is completeable only while ACTIVE and after end_time;
  * the winner is the participant with more votes.voted_for_user_id votes; an
    exact tie (including 0 vs 0) is a draw -> winner_id stays NULL;
  * completing preserves total_votes and prize_pool — no coin/ledger/reward
    writes happen here (prize distribution is a separate future feature).

Concurrency: complete_expired_competition takes a SELECT ... FOR UPDATE row
lock on the competition before re-checking status and counting votes. This
serializes against:
  * a concurrent manual/sweep completion of the same competition (the loser
    sees status = completed -> ALREADY_COMPLETED, never recomputes);
  * a concurrent cast_vote that is about to bump this row's
    total_votes/prize_pool — the vote either commits before the lock (and is
    included in the count) or, once this transaction commits, is rolled back
    by the vote endpoint's own `status = active` guard on its final write.

The `end_time` check uses the app's naive-UTC convention
(start_time + duration_minutes), independent of the DB session timezone — see
end_instant_utc.
"""
import enum
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import CoinTransaction, CoinTransactionType, Competition, CompetitionStatus, User, Vote

logger = logging.getLogger(__name__)

_REWARD_TYPE_NAME = "competition_reward"


def resolve_status_id(db: Session, status_name: str) -> int:
    """Resolve a competition_status lookup id by name (never hardcoding seed ids)."""
    status_id = db.scalar(
        select(CompetitionStatus.status_id).where(CompetitionStatus.status_name == status_name)
    )
    if status_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Competition status '{status_name}' is not configured",
        )
    return status_id


def end_instant_utc(competition: Competition) -> datetime:
    """The competition's end as an absolute instant, per the project's naive-UTC convention.

    The GENERATED `end_time` column is `start_time + duration` computed in the DB
    session's timezone, so its stored absolute instant is shifted by that offset.
    The app stores `start_time` as naive UTC and treats all naive timestamps as
    UTC, so the authoritative end is `start_time(UTC) + duration_minutes`. Using
    the wall-clock value keeps expirey independent of the session timezone.
    """
    return (competition.start_time + timedelta(minutes=competition.duration_minutes)).replace(
        tzinfo=timezone.utc
    )


class CompletionOutcome(enum.Enum):
    COMPLETED = "completed"              # newly finalized (winner recorded)
    ALREADY_COMPLETED = "already_completed"
    NOT_ACTIVE = "not_active"
    NOT_EXPIRED = "not_expired"
    NOT_FOUND = "not_found"


def complete_expired_competition(
    db: Session, competition_id: uuid.UUID
) -> tuple[CompletionOutcome, Competition | None]:
    """Complete a competition IF it is ACTIVE and its end_time has passed, and
    distribute its prize pool atomically (Phase 6, Part 4D).

    ONE transaction: winner calculation + status -> completed + prize payout
    (balance credits + competition_reward CREDIT ledger rows). total_votes and
    prize_pool are preserved on the competition.

    Payout:
      * a winner (winner_id != NULL) receives 100% of the prize pool;
      * a draw (winner_id == NULL) splits the pool equally between the two
        participants (the pool is always even = total_votes);
      * a zero-vote draw (prize_pool == 0) distributes nothing.

    Double-payout protection: the prize is ONLY distributed inside the guarded
    ACTIVE -> COMPLETED status transition, which is serialized by the SELECT
    ... FOR UPDATE row lock. A concurrent or repeated completion sees status =
    completed and returns ALREADY_COMPLETED without touching balances; a crash
    mid-transaction rolls the status change AND the payout back together, so
    "completed but never paid" and "paid twice" are both impossible.
    """
    active_id = resolve_status_id(db, "active")
    completed_id = resolve_status_id(db, "completed")

    competition = db.execute(
        select(Competition)
        .where(Competition.competition_id == competition_id)
        .with_for_update()
    ).scalar_one_or_none()
    if competition is None:
        return CompletionOutcome.NOT_FOUND, None
    if competition.status_id == completed_id:
        return CompletionOutcome.ALREADY_COMPLETED, competition
    if competition.status_id != active_id:
        return CompletionOutcome.NOT_ACTIVE, competition
    if datetime.now(timezone.utc) < end_instant_utc(competition):
        return CompletionOutcome.NOT_EXPIRED, competition

    # Winner = the participant with more votes received (voted_for_user_id).
    counts = dict(
        db.execute(
            select(Vote.voted_for_user_id, func.count())
            .where(
                Vote.competition_id == competition_id,
                Vote.voted_for_user_id.in_([competition.challenger_id, competition.opponent_id]),
            )
            .group_by(Vote.voted_for_user_id)
        ).all()
    )
    challenger_votes = counts.get(competition.challenger_id, 0)
    opponent_votes = counts.get(competition.opponent_id, 0)
    winner_id = None
    if challenger_votes > opponent_votes:
        winner_id = competition.challenger_id
    elif opponent_votes > challenger_votes:
        winner_id = competition.opponent_id

    competition.status_id = completed_id
    competition.winner_id = winner_id
    _distribute_prize(db, competition)
    db.commit()

    return CompletionOutcome.COMPLETED, competition


def _distribute_prize(db: Session, competition: Competition) -> None:
    """Credit the prize pool to the winner (or split it on a draw).

    Called only in the ACTIVE -> COMPLETED transition, before db.commit(), so
    the credits + ledger rows commit (or roll back) with the completion itself.
    Uses atomic `coin_balance = coin_balance + n RETURNING` updates and one
    positive competition_reward / CREDIT ledger row per recipient (balance_after
    from the RETURNING value) — the same pattern qr.py uses to credit coins.
    """
    prize = competition.prize_pool
    if prize <= 0:
        return  # zero-vote draw: nothing to distribute

    reward_type_id = db.scalar(
        select(CoinTransactionType.type_id).where(
            CoinTransactionType.type_name == _REWARD_TYPE_NAME
        )
    )
    if reward_type_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Competition reward transaction type is not configured",
        )

    if competition.winner_id is not None:
        _credit(
            db,
            user_id=competition.winner_id,
            amount=prize,
            reward_type_id=reward_type_id,
            competition_id=competition.competition_id,
        )
    else:
        # Draw: prize_pool == total_votes, always even.
        half = prize // 2
        _credit(
            db,
            user_id=competition.challenger_id,
            amount=half,
            reward_type_id=reward_type_id,
            competition_id=competition.competition_id,
        )
        _credit(
            db,
            user_id=competition.opponent_id,
            amount=half,
            reward_type_id=reward_type_id,
            competition_id=competition.competition_id,
        )


def _credit(
    db: Session,
    *,
    user_id: uuid.UUID,
    amount: int,
    reward_type_id: int,
    competition_id: uuid.UUID,
) -> None:
    new_balance = db.execute(
        update(User)
        .where(User.user_id == user_id)
        .values(coin_balance=User.coin_balance + amount)
        .returning(User.coin_balance)
    ).scalar_one()
    db.add(
        CoinTransaction(
            user_id=user_id,
            type_id=reward_type_id,
            amount=amount,  # positive = CREDIT
            balance_after=new_balance,
            competition_id=competition_id,
        )
    )


def sweep_expired_competitions(db: Session) -> int:
    """Complete every ACTIVE competition whose end_time has passed.

    Returns how many competitions were newly completed. Each competition goes
    through complete_expired_competition (row lock + status guard), so running
    concurrently with the manual endpoint or another sweeper - or re-running
    after everything is done - never double-completes or recomputes a winner.
    """
    active_id = resolve_status_id(db, "active")
    candidate_ids = db.execute(
        select(Competition.competition_id).where(Competition.status_id == active_id)
    ).scalars().all()

    completed_count = 0
    for competition_id in candidate_ids:
        outcome, _ = complete_expired_competition(db, competition_id)
        if outcome == CompletionOutcome.COMPLETED:
            completed_count += 1
    return completed_count


def run_expiration_sweeper(stop_event: threading.Event, interval_seconds: int) -> None:
    """Background loop: sweep expired ACTIVE competitions every interval.

    Runs one sweep immediately, then sleeps `interval_seconds` between sweeps.
    A sweep failure is logged and does not stop the loop. Never exits unless
    stop_event is set (daemon thread owned by the FastAPI lifespan).
    """
    while True:
        try:
            with SessionLocal() as db:
                sweep_expired_competitions(db)
        except Exception:  # noqa: BLE001 - background loop must keep running
            logger.exception("competition expiration sweep failed")
        if stop_event.wait(interval_seconds):
            break