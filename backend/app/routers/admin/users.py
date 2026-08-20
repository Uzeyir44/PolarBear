"""
Admin user management — list, inspect, and (de)activate accounts.

Covers:
  GET   /admin/users                 paginated list, newest first, with
                                     username/email search + is_active filter
  GET   /admin/users/{user_id}       full administrative detail
  PATCH /admin/users/{user_id}/status  deactivate / reactivate (is_active)

Every endpoint depends on get_current_admin(), so only administrators
reach them; normal users get 403 and unauthenticated callers 401.

Design notes
------------
- Users are NEVER deleted. Deactivation (is_active = false) is how an
  account is disabled — it matches the soft-delete design (get_current_user
  already rejects inactive users, so a disabled account cannot log in or
  hit protected endpoints).
- The only mutation this module supports is is_active. There is no path
  to edit username, email, password_hash, or coin_balance (each belongs to
  its own system; the coin_balance cache is owned by the transaction layer).
- An administrator cannot deactivate their own account: that would lock
  the operator out of the very endpoint doing the work.
- Search reuses escape_like() from the public /users/search endpoint so the
  admin and public surfaces share the same ILIKE escaping rules, but the
  admin schema additionally exposes email and accounting fields.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_admin
from app.models import User
from app.routers.users import escape_like
from app.schemas.user_admin import UserAdminList, UserAdminRead, UserAdminStatusUpdate

router = APIRouter(tags=["admin"])


@router.get("", response_model=UserAdminList)
def list_users(
    q: str | None = Query(
        default=None,
        max_length=50,
        description="Search fragment for username or email (partial, case-insensitive)",
    ),
    is_active: bool | None = Query(
        default=None,
        description="Filter by account status",
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="How many users to return (1-100)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="How many users to skip before returning results",
    ),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> UserAdminList:
    conditions = []
    if q:
        # Same escaping as public /users/search (escape_like), but the admin
        # surface matches BOTH username and email; CITEXT columns are already
        # case-insensitive, ilike just makes that explicit.
        pattern = f"%{escape_like(q.strip())}%"
        conditions.append(
            or_(
                User.username.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
            )
        )
    if is_active is not None:
        conditions.append(User.is_active.is_(is_active))

    total = db.execute(select(func.count(User.user_id)).where(*conditions)).scalar_one()
    users = db.execute(
        select(User)
        .where(*conditions)
        .order_by(User.created_at.desc(), User.user_id.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    return UserAdminList(items=users, total=total, limit=limit, offset=offset)


@router.get("/{user_id}", response_model=UserAdminRead)
def get_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


@router.patch("/{user_id}/status", response_model=UserAdminRead)
def update_user_status(
    user_id: uuid.UUID,
    payload: UserAdminStatusUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Guard against an operator locking themselves out of the admin panel.
    if user.user_id == current_admin.user_id and not payload.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account",
        )

    # Deliberately the ONLY field that can change through this endpoint.
    user.is_active = payload.is_active
    db.commit()
    db.refresh(user)
    return user