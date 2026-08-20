"""
User routes.

GET /me returns the profile of the currently authenticated user. It is the
first endpoint protected by get_current_user() and serves as the pattern
for every future endpoint that requires a logged-in user.

GET /me/coins returns the authenticated user's current coin_balance.
GET /me/transactions returns that user's coin_transactions ledger, newest
first, with limit/offset pagination. Both paths are fixed (the caller is
ALWAYS current_user.user_id from the JWT); neither endpoint accepts a
user_id from the client, so a user can never read someone else's money.

PATCH /me lets the authenticated user update their own profile. Only the
whitelisted "profile" fields can change here — identity/accounting fields
(user_id, email, password_hash, coin_balance, winning_streak, is_active,
created_at) are owned by other parts of the system and are never written
by this endpoint.

GET /search lets any authenticated user find OTHER users by partial
username match, returning only public profile fields. The caller is
excluded from its own results.

Follow endpoints (all authenticated, all use the URL's user_id as the
followee):
  - POST   /users/{user_id}/follow  create a follow relationship (201).
  - DELETE /users/{user_id}/follow  remove it (200).
  - GET    /users/{user_id}/follow-status  report whether the caller
           follows the user (200, {"is_following": bool}).

The follower is ALWAYS current_user.user_id from the JWT. The endpoint
declares no request body, so a client-supplied follower_id cannot affect
the operation. Duplicate follows are prevented structurally by the
composite primary key on (follower_id, followee_id); the application
pre-check just converts that into a friendly 409.

Search strategy tradeoff
------------------------
User search uses ILIKE '%q%' (case-insensitive substring match; the
CITEXT column already folds case, ILIKE just makes the intent explicit).
A '%q%' pattern cannot use the btree index on users.username because the
match is in the middle of the string and can appear anywhere in the
index's sort order — Postgres must scan every active row.

A btree index WOULD serve an equality ('alex') or prefix ('alex%') match
via a range scan. For efficient '%q%' at scale the standard answer is the
pg_trgm extension plus a GIN index with gin_trgm_ops on username, which
indexes 3-character trigrams and lets Postgres use the index for LIKE/
ILIKE patterns of 3+ characters.

Tradeoff: a GIN trigram index costs extra disk space and slower writes
(every INSERT/UPDATE must update it), requires enabling the pg_trgm
extension, and only starts paying off once the active-user table is large
(roughly 100k+ rows). At this project's scale a sequential scan over even
tens of thousands of users is sub-millisecond, so we keep the simple
query and add pg_trgm later if search becomes a bottleneck.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import CoinTransaction, Follow, QRCode, User
from app.schemas.coin import CoinBalance, CoinTransactionRead, QRTransactionReference
from app.schemas.user import FollowStatus, UserPublic, UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
def read_current_user(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.get("/me/coins", response_model=CoinBalance)
def get_my_coins(current_user: User = Depends(get_current_user)) -> CoinBalance:
    # current_user was already loaded by get_current_user() in this request's
    # session, so there is no extra query and no user_id is ever read from the
    # client. We only ever return the authenticated user's own balance.
    return CoinBalance(balance=current_user.coin_balance)


@router.get("/me/transactions", response_model=list[CoinTransactionRead])
def get_my_transactions(
    limit: int = Query(
        default=20,
        ge=1,
        le=50,
        description="How many transactions to return (1-50)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="How many transactions to skip before returning results",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CoinTransactionRead]:
    # user_id comes ONLY from the JWT -> get_current_user() ->
    # current_user.user_id; there is no user_id query/body path parameter.
    # The (user_id, created_at) composite index serves exactly this
    # WHERE user_id = ? + ORDER BY created_at pattern (a backward index
    # scan), so the DB does not sort the user's whole history.
    rows = db.execute(
        select(CoinTransaction)
        .where(CoinTransaction.user_id == current_user.user_id)
        .options(
            selectinload(CoinTransaction.type),
            selectinload(CoinTransaction.qr_code).joinedload(QRCode.product),
        )
        .order_by(CoinTransaction.created_at.desc(), CoinTransaction.transaction_id.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    return [_to_transaction_read(tx) for tx in rows]


def _to_transaction_read(tx: CoinTransaction) -> CoinTransactionRead:
    qr_ref = (
        QRTransactionReference(
            qr_id=tx.qr_code.qr_id,
            code=tx.qr_code.code,
            product_name=tx.qr_code.product.name,
        )
        if tx.qr_code is not None
        else None
    )
    return CoinTransactionRead(
        transaction_id=tx.transaction_id,
        amount=tx.amount,
        balance_after=tx.balance_after,
        # type_name "qr_redemption" / "refund" / ... ; direction's .name is
        # the DB-stored value ("CREDIT"/"DEBIT"), not the enum's lowercase
        # .value, so the API exposes exactly what the lookup table holds.
        transaction_type=tx.type.type_name,
        direction=tx.type.direction.name,
        created_at=tx.created_at,
        qr=qr_ref,
        competition_id=tx.competition_id,
        wardrobe_id=tx.wardrobe_id,
        vote_id=tx.vote_id,
    )


@router.get("/search", response_model=list[UserPublic])
def search_users(
    q: str = Query(
        min_length=1,
        max_length=30,
        description="Username fragment to search for (partial match)",
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=20,
        description="Maximum number of results to return",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[User]:
    # '%q%' cannot use the btree index on username (see strategy note in
    # the module docstring) — a sequential scan is intentional at this scale.
    pattern = f"%{escape_like(q)}%"
    users = db.execute(
        select(User)
        .where(
            User.is_active.is_(True),
            User.user_id != current_user.user_id,
            User.username.ilike(pattern, escape="\\"),
        )
        .order_by(User.username)
        .limit(limit)
    ).scalars().all()
    return users


def escape_like(value: str) -> str:
    # ILIKE treats % and _ as wildcards. We escape them so the user's input
    # is matched literally: searching "50%" should find usernames that
    # contain "50%", not "anything that starts with 50". Backslash is the
    # escape character we pass to SQLAlchemy below.
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.patch("/me", response_model=UserRead)
def update_current_user(
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    # Re-fetch the user in our own session so the row we mutate is attached
    # to the session we commit. (FastAPI shares one get_db session per
    # request, so this is usually the same object as current_user.)
    user = db.get(User, current_user.user_id)

    # exclude_unset=True returns a dict containing ONLY the fields the
    # client explicitly sent. An omitted field is not in the dict, so it is
    # never written — that's how a single-field PATCH leaves the others
    # untouched. Sending null for biography/profile_picture_url *is* an
    # explicit value, so it clears the column.
    updates = payload.model_dump(exclude_unset=True)

    # Friendly pre-check against the unique username constraint. CITEXT
    # makes the DB comparison case-insensitive, so "Alice" and "alice"
    # collide exactly as the constraint does. Reusing one's own username
    # (taken == our own id) is allowed.
    if "username" in updates:
        taken = db.execute(
            select(User.user_id).where(User.username == updates["username"])
        ).scalar()
        if taken is not None and taken != user.user_id:
            raise _username_conflict()

    for field, value in updates.items():
        setattr(user, field, value)

    try:
        db.commit()
    except IntegrityError as exc:
        # Races a concurrent registration/update that grabbed the username
        # between our check above and the commit.
        db.rollback()
        raise _username_conflict() from exc

    db.refresh(user)
    return user


def _username_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Username is already registered",
    )


@router.post(
    "/{user_id}/follow",
    response_model=FollowStatus,
    status_code=status.HTTP_201_CREATED,
)
def follow_user(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FollowStatus:
    # The URL's {user_id} is parsed by FastAPI as a uuid.UUID and is the ONLY
    # user ID the client ever controls. The follower comes exclusively from
    # the JWT -> get_current_user() -> current_user.user_id. There is no
    # request body, so a client cannot smuggle in a follower_id: if one is
    # sent it is simply ignored.
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot follow an inactive user",
        )

    if user_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot follow yourself",
        )

    # The follows table has a composite PRIMARY KEY (follower_id, followee_id),
    # so a duplicate follow is structurally impossible at the DB level. This
    # pre-check exists only to turn that constraint violation into a friendly
    # 409 before we attempt the INSERT.
    if db.get(Follow, (current_user.user_id, user_id)) is not None:
        raise _already_following()

    db.add(Follow(follower_id=current_user.user_id, followee_id=user_id))
    try:
        db.commit()
    except IntegrityError as exc:
        # Races a concurrent follow request that inserted the same row between
        # our pre-check and the commit above.
        db.rollback()
        raise _already_following() from exc

    return FollowStatus(is_following=True)


@router.delete("/{user_id}/follow", response_model=FollowStatus)
def unfollow_user(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FollowStatus:
    # Look up the relationship with our authenticated user as follower. There
    # is nothing to "un-find" if the target user doesn't exist either — a
    # follow row can't point at a nonexistent user (FK + ondelete CASCADE),
    # so the 404 below covers "target missing" and "not following" alike.
    follow = db.get(Follow, (current_user.user_id, user_id))
    if follow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not following this user",
        )

    db.delete(follow)
    db.commit()
    return FollowStatus(is_following=False)


@router.get("/{user_id}/follow-status", response_model=FollowStatus)
def follow_status(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FollowStatus:
    # Simply whether a follows row (me -> them) exists. Deliberately no
    # follower/following lists yet — that's a separate feature.
    is_following = db.get(Follow, (current_user.user_id, user_id)) is not None
    return FollowStatus(is_following=is_following)


def _already_following() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="You are already following this user",
    )