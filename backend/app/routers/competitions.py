"""
Competitions — Phase 6, Parts 2–3. Competition creation happens inside
acceptance (see routers/competition_requests.py); this router owns
retrieval and discovery.

  GET /competitions/{competition_id}   retrieve one competition (200)
  GET /competitions                    list the caller's competitions (200)

A competition is PRIVATE to its two participants at this stage. The detail
endpoint only lets the challenger or the opponent in (other authenticated
users get 403, unauthenticated get 401). Discovery ("my competitions") is
participant-scoped too — it filters WHERE current_user is challenger OR
opponent, so one user can never see competitions they are not part of.

GET /competitions supports an optional status filter (active|completed —
invalid values are rejected by FastAPI with 422) and limit/offset pagination
(the same convention as /users/me/transactions). Ordering is deterministic:
active competitions end soonest first (end_time ASC), completed most recently
ended first (end_time DESC), and an unfiltered list newest-created first
(created_at DESC, competition_id DESC tiebreak).

The payload is the safe CompetitionRead shape: both participants embed
UserPublic (never password_hash/email/coin_balance/streak/account fields),
status comes from the competition_status lookup row's status_name
("active"/"completed"), winner_id is null until completion is implemented.
"""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import Competition, CompetitionStatus, User
from app.schemas.competition import CompetitionRead
from app.schemas.user import UserPublic

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
            query.where(Competition.status_id == _status_id(db, "active"))
            .order_by(Competition.end_time.asc(), Competition.competition_id.asc())
        )
    elif status == "completed":
        query = (
            query.where(Competition.status_id == _status_id(db, "completed"))
            .order_by(Competition.end_time.desc(), Competition.competition_id.desc())
        )
    else:
        query = query.order_by(
            Competition.created_at.desc(), Competition.competition_id.desc()
        )

    rows = db.execute(query.limit(limit).offset(offset)).scalars().all()
    return [_to_competition_read(c) for c in rows]


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


def _status_id(db: Session, status_name: str) -> int:
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