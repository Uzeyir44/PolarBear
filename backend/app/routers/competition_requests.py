"""
Competition requests — Phase 6. The pre-competition negotiation lifecycle:
send, list (incoming/outgoing), accept, decline, cancel.

Endpoints (all require a valid JWT; participant identity comes only from the
token via get_current_user(), never from a client-supplied id):

  POST /competition-requests                     send a challenge (201)
  GET  /competition-requests/incoming            requests sent TO me
  GET  /competition-requests/outgoing            requests sent BY me
  POST /competition-requests/{id}/accept         opponent: PENDING -> ACCEPTED
                                                 AND creates an ACTIVE competition
  POST /competition-requests/{id}/decline        opponent: PENDING -> DECLINED
  POST /competition-requests/{id}/cancel         challenger: PENDING -> CANCELLED

Acceptance (Part 2)
-------------------
Accepting a PENDING request also creates the corresponding `competitions`
row and both happen in ONE database transaction:

    BEGIN
        conditional request -> ACCEPTED (status = 'PENDING' guard)
        INSERT competition (request_id, parties, duration, status = active,
                            start_time = now; end_time is a DB GENERATED
                            column, prize_pool/total_votes use the server
                            defaults 0 and 0, winner_id = NULL)
    COMMIT           — or ROLLBACK if anything fails

The request can never be left ACCEPTED without its competition, and a
competition can never exist while its request is still PENDING. Duplicate
protection is belt-and-braces: the `competitions.request_id` UNIQUE
constraint guarantees one competition per request, and the conditional
PENDING UPDATE guarantees that of two concurrent acceptance attempts only
one ever reaches competition creation.

Active-competition limits (Part 3)
----------------------------------
Before inserting the competition the accept path enforces two business rules
against `status = 'active'` competitions only (completed ones never count):

  1. a user may participate in at most 3 active competitions at a time;
  2. the same unordered pair may have at most 1 active competition.

Both participants are checked (challenger AND opponent); if either rule
fails, the accept returns 409 and the request is left PENDING (so it can be
accepted later, after an active competition completes or the matchup ends) —
the request is never auto-DECLINED. Concurrency-safety: the accept takes
SELECT ... FOR UPDATE locks on both participant user rows (sorted, so
deadlock-free) before any counting, so two simultaneous accepts involving the
same user serialize on that user's row and a stale active-count read becomes
impossible. See _enforce_active_rules.

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
race exactly one wins; the loser sees rowcount == 0 and returns 409. Because
the competition INSERT only ever runs after the conditional UPDATE reports a
win for THIS transaction, a lost race never creates a competition row — the
`request_id` UNIQUE constraint is a second line of defense. No explicit row
lock is needed — the conditional update itself serializes the transition.

Inactive accounts: following the project's established interpretation,
get_current_user() 401s inactive users entirely, so an inactive account can
neither send requests nor act on them (no extra code needed here).
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import Competition, CompetitionRequest, CompetitionRequestStatus, CompetitionStatus, User
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

    # Part 2+3: acceptance also creates the competition. Only the request that
    # WON the conditional transition above reaches this INSERT, so a lost
    # concurrent acceptance never produces a competition row. Everything —
    # the request UPDATE and this INSERT — commits (or rolls back) together.
    if action == "accept":
        active_status_id = _active_status_id(db)
        if active_status_id is None:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Active competition status is not configured",
            )
        _enforce_active_rules(
            db,
            challenger_id=request.challenger_id,
            opponent_id=request.opponent_id,
            active_status_id=active_status_id,
        )
        # prize_pool/total_votes use the DB server defaults (0 / 0),
        # winner_id stays NULL; end_time is a GENERATED
        # column the database computes from start_time + duration_minutes.
        db.add(
            Competition(
                request_id=request_id,
                challenger_id=request.challenger_id,
                opponent_id=request.opponent_id,
                duration_minutes=request.duration_minutes,
                start_time=now,
                status_id=active_status_id,
            )
        )

    try:
        db.commit()
    except IntegrityError as exc:
        # The request_id UNIQUE constraint on competitions fired — a
        # competition for this request already exists (legacy/pre-seeded row).
        # The whole transaction rolls back, so the request stays PENDING and
        # the pre-existing competition survives untouched.
        db.rollback()
        raise _competition_conflict() from exc

    db.refresh(request)
    return _to_request_read(request)


def _active_status_id(db: Session) -> int | None:
    # Resolves the lookup row by name (never hardcoding seed ids), mirroring
    # how qr.py resolves its coin_transaction_types row.
    return db.scalar(
        select(CompetitionStatus.status_id).where(CompetitionStatus.status_name == "active")
    )


def _enforce_active_rules(
    db: Session,
    *,
    challenger_id: uuid.UUID,
    opponent_id: uuid.UUID,
    active_status_id: int,
) -> None:
    """Serialize and validate active-competition limits for a new competition.

    Concurrency safety: take SELECT ... FOR UPDATE row locks on BOTH
    participant user rows (in sorted order, so concurrent transactions can
    never deadlock) BEFORE counting. Every competition-creation transaction
    follows the same locking protocol, so any accept involving a user is
    serialized with every other accept involving that same user — a concurrent
    accept can no longer read a stale active-count. This is the qr.py redeem
    pattern ("the lock, not the Python check, is what makes it safe").

    Rules enforced (status_id = 'active' only):
      1. neither participant may already be in 3 active competitions;
      2. the unordered pair may not already have an active competition.
    """
    _lock_participants(db, challenger_id, opponent_id)

    for participant in (challenger_id, opponent_id):
        active_count = db.scalar(
            select(func.count())
            .select_from(Competition)
            .where(
                Competition.status_id == active_status_id,
                or_(
                    Competition.challenger_id == participant,
                    Competition.opponent_id == participant,
                ),
            )
        )
        if active_count >= 3:
            raise _active_limit_reached()

    matchup_count = db.scalar(
        select(func.count())
        .select_from(Competition)
        .where(
            Competition.status_id == active_status_id,
            or_(
                and_(
                    Competition.challenger_id == challenger_id,
                    Competition.opponent_id == opponent_id,
                ),
                and_(
                    Competition.challenger_id == opponent_id,
                    Competition.opponent_id == challenger_id,
                ),
            ),
        )
    )
    if matchup_count:
        raise _duplicate_active_matchup()


def _lock_participants(db: Session, challenger_id: uuid.UUID, opponent_id: uuid.UUID) -> None:
    # Ordered by user_id so every transaction locks shared users in the same
    # order — two transactions locking {A, B} and {B, A} both take A's lock
    # first, which is what makes the protocol deadlock-free.
    db.execute(
        select(User.user_id)
        .where(User.user_id.in_([challenger_id, opponent_id]))
        .order_by(User.user_id)
        .with_for_update()
    )


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


def _competition_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A competition for this request already exists",
    )


def _active_limit_reached() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="The active competition limit has been reached (maximum 3)",
    )


def _duplicate_active_matchup() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="These users already have an active competition",
    )