"""
Authentication routes.

For now this only covers registration. Login, tokens, and OAuth come in
later batches — deliberately not implemented here.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import password_hasher
from app.models import User
from app.schemas.user import UserRead, UserRegister

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
def register(payload: UserRegister, db: Session = Depends(get_db)) -> User:
    existing_username = db.execute(
        select(User.user_id).where(User.username == payload.username)
    ).scalar()
    if existing_username is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already registered",
        )

    existing_email = db.execute(
        select(User.user_id).where(User.email == payload.email)
    ).scalar()
    if existing_email is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        )

    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=password_hasher.hash(payload.password),
    )

    try:
        db.add(user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _duplicate_http_exception(exc) from exc

    db.refresh(user)
    return user


def _duplicate_http_exception(exc: IntegrityError) -> HTTPException:
    """Map a DB unique-violation to a 409. Covers the race between our
    check and the insert, without relying on Postgres error strings."""
    constraint = getattr(exc.orig, "diag", None)
    constraint_name = getattr(constraint, "constraint_name", None)

    if constraint_name == "ix_users_username":
        detail = "Username is already registered"
    elif constraint_name == "ix_users_email":
        detail = "Email is already registered"
    else:
        detail = "Email or username is already registered"

    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)