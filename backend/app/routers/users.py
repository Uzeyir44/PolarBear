"""
User routes.

GET /me returns the profile of the currently authenticated user. It is the
first endpoint protected by get_current_user() and serves as the pattern
for every future endpoint that requires a logged-in user.

PATCH /me lets the authenticated user update their own profile. Only the
whitelisted "profile" fields can change here — identity/accounting fields
(user_id, email, password_hash, coin_balance, winning_streak, is_active,
created_at) are owned by other parts of the system and are never written
by this endpoint.

GET /search lets any authenticated user find OTHER users by partial
username match, returning only public profile fields. The caller is
excluded from its own results.

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
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas.user import UserPublic, UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
def read_current_user(current_user: User = Depends(get_current_user)) -> User:
    return current_user


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