"""
JWT access-token helpers.

A JWT is a signed, base64 JSON object. We only put minimal claims in it:
"sub" (subject = the user's id), "iat" (issued at), and "exp" (expiry).
Sensitive data like the password hash never belongs in a token.
"""
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

ALGORITHM = "HS256"


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)

    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str:
    """Return the user_id from a valid token, or raise jwt.InvalidTokenError
    (which also covers expired and tampered tokens)."""
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])

    subject = payload.get("sub")
    if subject is None:
        raise jwt.InvalidTokenError("Token is missing the 'sub' claim")

    return subject