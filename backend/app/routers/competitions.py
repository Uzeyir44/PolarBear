"""
Competitions — Phase 6, Parts 2–4. Competition creation happens inside
acceptance (see routers/competition_requests.py); this router owns
retrieval and discovery.

  GET /competitions/{competition_id}   retrieve one competition (200, participant-only)
  GET /competitions                    list the caller's competitions (200)
  GET /competitions/discover           public feed of OTHER users' ACTIVE competitions (200)
  POST /competitions/{id}/complete     manual completion trigger (200, participant-only)

The detail endpoint only lets the challenger or the opponent in (other
authenticated users get 403, unauthenticated get 401). GET /competitions is
participant-scoped too — the caller only ever sees competitions they are in.

GET /competitions/discover is the vote-stage discovery feed: ACTIVE
competitions between two OTHER ACTIVE users, so the authenticated caller can
open (and, in Part 4B, vote on) competitions without being a participant. It
excludes the caller's own competitions (challenger OR opponent — a
participant cannot vote in their own competition), completed competitions,
and competitions whose challenger or opponent account is inactive. Response
uses the wardrobe/clothing-style envelope {items, total, limit, offset},
ordered by end_time ASC (ending soonest first) with a competition_id tiebreak.

The payload is the safe CompetitionRead shape: both participants embed
UserPublic (never password_hash/email/coin_balance/streak/account fields),
status comes from the competition_status lookup row's status_name
("active"/"completed"), winner_id is null until completion is implemented.

Route ordering note: /discover is a STATIC path and MUST be declared before
/{competition_id}, otherwise FastAPI would treat "discover" as a UUID and
reject it with 422.
"""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import Competition, User
from app.schemas.competition import CompetitionDiscoverResult, CompetitionRead
from app.schemas.user import UserPublic
from app.services.competition_expiration import (
    CompletionOutcome,
    complete_expired_competition,
    resolve_status_id,
)

router = APIRouter(prefix="/competitions", tags=["competitions"])

CompetitionStatusFilter = Literal["active", "completed"]


@router.get("", response_model=list[CompetitionRead])
def list_competitions(
    status: CompetitionStatusFilter | None = Query(
        default=None,
        description="Only return competitions in this status ('active' or 'completed')",
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=50,
        description="How many competitions to return (1-50)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="How many competitions to skip before returning results",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CompetitionRead]:
    # Participant-scoped: the caller only ever sees competitions they are in.
    # The user_id comes ONLY from the JWT; there is no client-supplied id.
    query = (
        select(Competition)
        .where(
            or_(
                Competition.challenger_id == current_user.user_id,
                Competition.opponent_id == current_user.user_id,
            )
        )
        .options(
            joinedload(Competition.challenger),
            joinedload(Competition.opponent),
            joinedload(Competition.status),
        )
    )

    if status == "active":
        query = (
            query.where(Competition.status_id == resolve_status_id(db, "active"))
            .order_by(Competition.end_time.asc(), Competition.competition_id.asc())
        )
    elif status == "completed":
        query = (
            query.where(Competition.status_id == resolve_status_id(db, "completed"))
            .order_by(Competition.end_time.desc(), Competition.competition_id.desc())
        )
    else:
        query = query.order_by(
            Competition.created_at.desc(), Competition.competition_id.desc()
        )

    rows = db.execute(query.limit(limit).offset(offset)).scalars().all()
    return [_to_competition_read(c) for c in rows]


@router.get("/discover", response_model=CompetitionDiscoverResult)
def discover_competitions(
    limit: int = Query(
        default=20,
        ge=1,
        le=50,
        description="How many competitions to return (1-50)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="How many competitions to skip before returning results",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitionDiscoverResult:
    # The public feed: ACTIVE competitions between OTHER active users, so the
    # caller has something to open (and later vote on) without being a
    # participant. Excludes the caller's own competitions (own challenger OR
    # opponent — a participant can never vote themselves) and competitions
    # whose challenger or opponent account is inactive. status/participant
    # filters are applied on FK/filter columns (challenger_id/opponent_id),
    # never on anything the client controls.
    active_id = resolve_status_id(db, "active")
    conditions = [
        Competition.status_id == active_id,
        Competition.challenger_id != current_user.user_id,
        Competition.opponent_id != current_user.user_id,
        Competition.challenger.has(User.is_active.is_(True)),
        Competition.opponent.has(User.is_active.is_(True)),
    ]

    total = db.scalar(select(func.count()).select_from(Competition).where(*conditions))
    rows = db.execute(
        select(Competition)
        .where(*conditions)
        .options(
            joinedload(Competition.challenger),
            joinedload(Competition.opponent),
            joinedload(Competition.status),
        )
        # Ending soonest first — the most time-sensitive to vote on — with a
        # deterministic competition_id tiebreak.
        .order_by(Competition.end_time.asc(), Competition.competition_id.asc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    return CompetitionDiscoverResult(
        items=[_to_competition_read(c) for c in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{competition_id}", response_model=CompetitionRead)
def get_competition(
    competition_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitionRead:
    competition = db.execute(
        select(Competition)
        .where(Competition.competition_id == competition_id)
        .options(
            joinedload(Competition.challenger),
            joinedload(Competition.opponent),
            joinedload(Competition.status),
        )
    ).scalar_one_or_none()
    if competition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Competition not found",
        )

    # Private to the participants only. The participant check reads the two FK
    # columns, not the joined users, so it cannot be influenced by client input.
    if (
        competition.challenger_id != current_user.user_id
        and competition.opponent_id != current_user.user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a participant of this competition",
        )

    return _to_competition_read(competition)


@router.post("/{competition_id}/complete", response_model=CompetitionRead)
def complete_competition(
    competition_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitionRead:
    """Manually finalize an expired competition and calculate its winner.

    This is an administrative/manual completion trigger. In normal operation
    the backend's automatic expiration sweeper (services/competition_expiration
    .run_expiration_sweeper) completes competitions once end_time passes, so
    the mobile client does not need to call this endpoint. Both paths share the
    same complete_expired_competition service, so winner/draw determination and
    the ACTIVE/end_time/status guards are identical.

    Requires authentication; only the two participants may trigger it (403 for
    anyone else). Completion does NOT distribute the prize pool or touch coins.
    """
    competition = db.get(Competition, competition_id)
    if competition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Competition not found",
        )
    if (
        competition.challenger_id != current_user.user_id
        and competition.opponent_id != current_user.user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a participant of this competition",
        )

    outcome, _ = complete_expired_competition(db, competition_id)
    if outcome == CompletionOutcome.ALREADY_COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Competition is already completed",
        )
    if outcome == CompletionOutcome.NOT_ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Competition is not active",
        )
    if outcome == CompletionOutcome.NOT_EXPIRED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Competition has not finished yet",
        )

    completed = db.execute(
        select(Competition)
        .where(Competition.competition_id == competition_id)
        .options(
            joinedload(Competition.challenger),
            joinedload(Competition.opponent),
            joinedload(Competition.status),
        )
    ).scalar_one()
    return _to_competition_read(completed)


def _to_competition_read(competition: Competition) -> CompetitionRead:
    return CompetitionRead(
        competition_id=competition.competition_id,
        request_id=competition.request_id,
        challenger=UserPublic.model_validate(competition.challenger),
        opponent=UserPublic.model_validate(competition.opponent),
        status=competition.status.status_name,
        prize_pool=competition.prize_pool,
        total_votes=competition.total_votes,
        winner_id=competition.winner_id,
        duration_minutes=competition.duration_minutes,
        start_time=competition.start_time,
        end_time=competition.end_time,
        created_at=competition.created_at,
    )