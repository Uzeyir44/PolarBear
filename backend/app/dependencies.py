"""
Reusable auth dependencies.

get_current_user is the gatekeeper for protected endpoints. It reads the
Authorization: Bearer <token> header, validates the JWT, and loads the
user. Any endpoint that needs a logged-in user just declares:

    current_user: User = Depends(get_current_user)
"""
import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.jwt import decode_access_token
from app.models import User

# Extracts the Bearer token from the request's Authorization header. If the
# header is missing the OAuth2PasswordBearer dependency returns a 401 itself.
# tokenUrl points at the login endpoint, for Swagger's "Authorize" flow.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        user_id = uuid.UUID(decode_access_token(token))
    except (jwt.InvalidTokenError, ValueError):
        raise credentials_exception

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise credentials_exception

    return user