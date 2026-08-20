"""
End-to-end check of login + JWT auth: register -> login -> /users/me.

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_auth_flow

Covers: successful login, JWT claims, protected /users/me, wrong password,
nonexistent user, missing/invalid/expired token, inactive user. Test users
are deleted afterwards so the development database is left clean.
"""
import time
import uuid
import warnings
from datetime import datetime, timedelta, timezone

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

import jwt as pyjwt
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import User

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
USERNAME = f"authuser_{RUN_ID}"
EMAIL = f"auth_{RUN_ID}@example.com"
PASSWORD = "SuperSecret123!"
SECRET = settings.secret_key
ALGO = "HS256"

created_usernames = set()


def report(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({extra})" if extra else ""))


def db_user_by_username(username: str) -> User | None:
    with SessionLocal() as db:
        return db.execute(select(User).where(User.username == username)).scalar_one_or_none()


def cleanup() -> None:
    with SessionLocal() as db:
        for username in created_usernames:
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is not None:
                db.delete(user)
        db.commit()
    print(f"\nCleaned up {len(created_usernames)} test user(s).")


def make_token(user_id: str, expires_delta: timedelta) -> str:
    """Build a JWT like the app would, for negative tests (expired, bogus user)."""
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + expires_delta,
    }
    return pyjwt.encode(payload, SECRET, algorithm=ALGO)


results = []

# --- Setup: register a user -------------------------------------------------
response = client.post(
    "/auth/register",
    json={"username": USERNAME, "email": EMAIL, "password": PASSWORD},
)
created_usernames.add(USERNAME)
results.append(("register test user", response.status_code == 201, str(response.status_code)))

# --- 1. Login with correct credentials ---------------------------------------
response = client.post(
    "/auth/login",
    json={"username": USERNAME, "password": PASSWORD},
)
ok = response.status_code == 200 and response.json().get("access_token")
results.append(("login returns 200 with access_token", ok, str(response.status_code)))
token = response.json().get("access_token", "")

# --- 2. JWT contents ----------------------------------------------------------
payload = pyjwt.decode(token, SECRET, algorithms=[ALGO])
results.append(("token has sub claim = user_id", payload.get("sub") is not None, str(payload.get("sub"))))
results.append(("token has exp claim", "exp" in payload, "yes" if "exp" in payload else "no"))
results.append(
    ("token carries no password/hash/secret",
     all(k not in payload for k in ("password", "password_hash", "secret_key")),
     str(sorted(payload.keys()))),
)

# --- 3. /users/me with valid token --------------------------------------------
response = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
body = response.json()
ok = response.status_code == 200 and body.get("username") == USERNAME
results.append(("GET /users/me with valid token returns user", ok, str(response.status_code)))
results.append(
    ("me response has no password/hash",
     "password" not in body and "password_hash" not in body,
     str(sorted(body.keys()))),
)

# --- 4. Wrong password ---------------------------------------------------------
response = client.post(
    "/auth/login",
    json={"username": USERNAME, "password": "WrongPassword!1"},
)
ok = response.status_code == 401
results.append(("wrong password rejected with 401", ok, str(response.status_code)))

# --- 5. Nonexistent user -------------------------------------------------------
response = client.post(
    "/auth/login",
    json={"username": f"nobody_{RUN_ID}", "password": PASSWORD},
)
ok = response.status_code == 401
results.append(("nonexistent user login rejected with 401", ok, str(response.status_code)))

# --- 6. Missing token -----------------------------------------------------------
response = client.get("/users/me")
ok = response.status_code == 401
results.append(("missing token -> 401", ok, str(response.status_code)))

# --- 7. Invalid token ------------------------------------------------------------
response = client.get("/users/me", headers={"Authorization": "Bearer not.a.jwt"})
ok = response.status_code == 401
results.append(("invalid token -> 401", ok, str(response.status_code)))

# --- 8. Expired token ------------------------------------------------------------
expired = make_token(str(uuid.uuid4()), timedelta(minutes=-5))
response = client.get("/users/me", headers={"Authorization": f"Bearer {expired}"})
ok = response.status_code == 401
results.append(("expired token -> 401", ok, str(response.status_code)))

# --- 9. Token for nonexistent user -----------------------------------------------
bogus_user_token = make_token(str(uuid.uuid4()), timedelta(minutes=5))
response = client.get("/users/me", headers={"Authorization": f"Bearer {bogus_user_token}"})
ok = response.status_code == 401
results.append(("token for nonexistent user -> 401", ok, str(response.status_code)))

# --- 10. Inactive user -------------------------------------------------------------
user = db_user_by_username(USERNAME)
user_id = user.user_id
with SessionLocal() as db:
    db_user = db.get(User, user_id)
    db_user.is_active = False
    db.commit()
valid_user_token = make_token(str(user_id), timedelta(minutes=5))
response = client.get("/users/me", headers={"Authorization": f"Bearer {valid_user_token}"})
ok = response.status_code == 401
results.append(("token for inactive user -> 401", ok, str(response.status_code)))

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")

cleanup()

if failed:
    raise SystemExit(1)