"""
Authentication routes.

Registration and login. Access tokens are returned on login and then used
by protected endpoints through get_current_user(). Refresh tokens, Google/
Apple login, logout/session revocation, and MFA are deliberately not part
of the MVP and are not implemented here.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.jwt import create_access_token
from app.core.security import hash_password, verify_password
from app.models import User
from app.schemas.token import LoginRequest, Token
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
        password_hash=hash_password(payload.password),
    )

    try:
        db.add(user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _duplicate_http_exception(exc) from exc

    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> Token:
    user = db.execute(
        select(User).where(
            or_(User.username == payload.username, User.email == payload.username)
        )
    ).scalar_one_or_none()

    # One message for both "bad user" and "bad password" so an attacker
    # can't tell from the error whether an account exists (no enumeration).
    if (
        user is None
        or user.password_hash is None
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return Token(access_token=create_access_token(user.user_id))


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