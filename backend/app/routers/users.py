"""
User routes.

/me returns the profile of the currently authenticated user. It is the
first endpoint protected by get_current_user() and serves as the pattern
for every future endpoint that requires a logged-in user.

PATCH /me lets the authenticated user update their own profile. Only the
whitelisted "profile" fields can change here — identity/accounting fields
(user_id, email, password_hash, coin_balance, winning_streak, is_active,
created_at) are owned by other parts of the system and are never written
by this endpoint.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas.user import UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
def read_current_user(current_user: User = Depends(get_current_user)) -> User:
    return current_user


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