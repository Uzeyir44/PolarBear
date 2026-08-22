"""
Competition requests — Phase 6, Part 1. The pre-competition negotiation
lifecycle ONLY: send, list (incoming/outgoing), accept, decline, cancel.

Endpoints (all require a valid JWT; participant identity comes only from the
token via get_current_user(), never from a client-supplied id):

  POST /competition-requests                     send a challenge (201)
  GET  /competition-requests/incoming            requests sent TO me
  GET  /competition-requests/outgoing            requests sent BY me
  POST /competition-requests/{id}/accept         opponent: PENDING -> ACCEPTED
  POST /competition-requests/{id}/decline        opponent: PENDING -> DECLINED
  POST /competition-requests/{id}/cancel         challenger: PENDING -> CANCELLED

Authorization rules
-------------------
- Sending: challenger is ALWAYS current_user.user_id from the JWT. The client
  only supplies opponent_id + duration_minutes. Opponent must exist and be
  active; challenger active is guaranteed by get_current_user() (which 401s
  inactive accounts).
- Accept/decline: ONLY the opponent (the request's recipient) may act.
- Cancel: ONLY the challenger (the request's sender) may act.
- A request may only transition out of PENDING once.

Duplicate / active requests
---------------------------
The competition-requests model explicitly documents "multiple pending
requests are allowed" — there is deliberately no uniqueness constraint, and
when a request is later accepted a future batch cancels the other pending
requests involving either participant in the same transaction. Following
that existing design, a second PENDING request from the same challenger to
the same opponent is simply created, and A->B + B->A can both be PENDING
simultaneously. No application-level duplicate check, no new migration.

Status lifecycle
----------------
PENDING -> ACCEPTED / DECLINED / CANCELLED. Once a request leaves PENDING it
can never transition again (see _transition_request). responded_at stays NULL
while PENDING and is set (naive UTC, matching the qr redeem convention) when
status changes.

Concurrency
-----------
accept/decline/cancel use an atomic conditional UPDATE
(UPDATE ... WHERE status = 'PENDING' ...) and verify exactly one row was
affected. With READ COMMITTED, Postgres re-evaluates the WHERE against the
latest committed row version after acquiring the row lock, so if two requests
race (accept + decline) exactly one wins; the loser sees rowcount == 0 and
returns 409. No explicit row lock is needed — the conditional update itself
serializes the transition, the lightest safe option.

Inactive accounts: following the project's established interpretation,
get_current_user() 401s inactive users entirely, so an inactive account can
neither send requests nor act on them (no extra code needed here).
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import CompetitionRequest, CompetitionRequestStatus, User
from app.schemas.competition_request import CompetitionRequestCreate, CompetitionRequestRead
from app.schemas.user import UserPublic

router = APIRouter(prefix="/competition-requests", tags=["competition-requests"])


@router.post("", response_model=CompetitionRequestRead, status_code=status.HTTP_201_CREATED)
def send_competition_request(
    payload: CompetitionRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitionRequestRead:
    # The challenger comes ONLY from the JWT -> get_current_user(). The body
    # carries just opponent_id + duration_minutes; a client-supplied
    # challenger_id is ignored (it is not a field of the schema).
    opponent = db.get(User, payload.opponent_id)
    if opponent is None:
        raise _user_not_found()

    if not opponent.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot send a request to an inactive user",
        )

    if payload.opponent_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot challenge yourself",
        )

    # status defaults to PENDING (column server_default) and created_at to now().
    request = CompetitionRequest(
        challenger_id=current_user.user_id,
        opponent_id=payload.opponent_id,
        duration_minutes=payload.duration_minutes,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return _to_request_read(request)


@router.get("/incoming", response_model=list[CompetitionRequestRead])
def list_incoming_requests(
    limit: int = Query(
        default=20,
        ge=1,
        le=50,
        description="How many requests to return (1-50)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="How many requests to skip before returning results",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CompetitionRequestRead]:
    # opponent_id comes ONLY from the JWT: a user can only ever see requests
    # addressed to themselves.
    rows = db.execute(
        select(CompetitionRequest)
        .where(CompetitionRequest.opponent_id == current_user.user_id)
        .options(
            joinedload(CompetitionRequest.challenger),
            joinedload(CompetitionRequest.opponent),
        )
        .order_by(CompetitionRequest.created_at.desc(), CompetitionRequest.request_id.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()
    return [_to_request_read(r) for r in rows]


@router.get("/outgoing", response_model=list[CompetitionRequestRead])
def list_outgoing_requests(
    limit: int = Query(
        default=20,
        ge=1,
        le=50,
        description="How many requests to return (1-50)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="How many requests to skip before returning results",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CompetitionRequestRead]:
    # challenger_id comes ONLY from the JWT: a user can only ever see requests
    # they themselves sent.
    rows = db.execute(
        select(CompetitionRequest)
        .where(CompetitionRequest.challenger_id == current_user.user_id)
        .options(
            joinedload(CompetitionRequest.challenger),
            joinedload(CompetitionRequest.opponent),
        )
        .order_by(CompetitionRequest.created_at.desc(), CompetitionRequest.request_id.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()
    return [_to_request_read(r) for r in rows]


@router.post("/{request_id}/accept", response_model=CompetitionRequestRead)
def accept_competition_request(
    request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitionRequestRead:
    return _transition_request(db, request_id, current_user, role="opponent", action="accept")


@router.post("/{request_id}/decline", response_model=CompetitionRequestRead)
def decline_competition_request(
    request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitionRequestRead:
    return _transition_request(db, request_id, current_user, role="opponent", action="decline")


@router.post("/{request_id}/cancel", response_model=CompetitionRequestRead)
def cancel_competition_request(
    request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitionRequestRead:
    return _transition_request(db, request_id, current_user, role="challenger", action="cancel")


_TARGET_STATUS = {
    "accept": CompetitionRequestStatus.ACCEPTED,
    "decline": CompetitionRequestStatus.DECLINED,
    "cancel": CompetitionRequestStatus.CANCELLED,
}


def _transition_request(
    db: Session,
    request_id: uuid.UUID,
    current_user: User,
    role: str,
    action: str = "accept",
) -> CompetitionRequestRead:
    """PENDING -> terminal state, guarded so the transition can only happen once.

    accept/decline both act as the "opponent"; cancel acts as the "challenger".
    """
    participant_col = (
        CompetitionRequest.opponent_id
        if role == "opponent"
        else CompetitionRequest.challenger_id
    )
    target_status = _TARGET_STATUS[action]

    request = db.get(CompetitionRequest, request_id)
    if request is None:
        raise _request_not_found()

    if getattr(request, f"{role}_id") != current_user.user_id:
        raise _not_participant(role)

    if request.status != CompetitionRequestStatus.PENDING:
        raise _no_longer_pending()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = db.execute(
        update(CompetitionRequest)
        .where(
            CompetitionRequest.request_id == request_id,
            participant_col == current_user.user_id,
            CompetitionRequest.status == CompetitionRequestStatus.PENDING,
        )
        .values(status=target_status, responded_at=now)
    )
    if result.rowcount == 0:
        # Lost a concurrent accept/decline/cancel race — the row moved out of
        # PENDING between our check and the atomic UPDATE.
        db.rollback()
        raise _no_longer_pending()

    db.commit()
    db.refresh(request)
    return _to_request_read(request)


def _to_request_read(request: CompetitionRequest) -> CompetitionRequestRead:
    return CompetitionRequestRead(
        request_id=request.request_id,
        challenger=UserPublic.model_validate(request.challenger),
        opponent=UserPublic.model_validate(request.opponent),
        duration_minutes=request.duration_minutes,
        status=request.status.name,
        created_at=request.created_at,
        responded_at=request.responded_at,
    )


def _user_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="User not found",
    )


def _request_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Competition request not found",
    )


def _not_participant(role: str) -> HTTPException:
    detail = (
        "You are not the opponent of this request"
        if role == "opponent"
        else "You are not the challenger of this request"
    )
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
    )


def _no_longer_pending() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Competition request is no longer pending",
    )