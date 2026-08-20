"""
End-to-end check of GET /users/search — authenticated partial username search.

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_user_search

Covers: partial (prefix and mid-string) match, case-insensitive match,
no matches -> [], missing/invalid/expired token -> 401, empty/invalid
query -> 422, limit validation, inactive users excluded, and responses
carry only public fields (never email/password/coin/streak). Test users
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
PASSWORD = "SuperSecret123!"

# Prefix match for "alex" ...
USER_A = f"alex{RUN_ID}"
# ... mid-string match (contains RUN_ID and "_alex" at the end)
USER_B = f"zzz{RUN_ID}_alex"
# Inactive user that must never appear in results
USER_INACTIVE = f"alex_inactive_{RUN_ID}"
# Non-matching username
USER_OTHER = f"bear{RUN_ID}"

USERS = [
    (USER_A, f"{USER_A}@example.com"),
    (USER_B, f"{USER_B}@example.com"),
    (USER_INACTIVE, f"{USER_INACTIVE}@example.com"),
    (USER_OTHER, f"{USER_OTHER}@example.com"),
]


def report(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({extra})" if extra else ""))


def cleanup() -> None:
    with SessionLocal() as db:
        for row in db.execute(select(User).where(User.username.in_([u for u, _ in USERS]))).scalars().all():
            db.delete(row)
        db.commit()
    print(f"\nCleaned up {len(USERS)} test user(s).")


def make_token(user_id: str, expires_delta: timedelta) -> str:
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + expires_delta,
    }
    return pyjwt.encode(payload, settings.secret_key, algorithm="HS256")


results = []

# --- Setup: register users ------------------------------------------------
for username, email in USERS:
    response = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": PASSWORD},
    )
    results.append((f"setup register {username}", response.status_code == 201, str(response.status_code)))

# Deactivate the "inactive" user directly in the DB.
with SessionLocal() as db:
    user_inactive = db.execute(
        select(User).where(User.username == USER_INACTIVE)
    ).scalar_one()
    user_inactive.is_active = False
    db.commit()

response = client.post("/auth/login", json={"username": USER_A, "password": PASSWORD})
token = response.json().get("access_token", "")
AUTH = {"Authorization": f"Bearer {token}"}
results.append(("setup login works", bool(token), ""))


def usernames(resp) -> set[str]:
    return {item["username"] for item in resp.json()}


# --- 1. Authenticated partial search (prefix + mid-string) -------------------
response = client.get("/users/search", params={"q": "alex"}, headers=AUTH)
ok = (
    response.status_code == 200
    and {USER_B} <= usernames(response)
    and USER_A not in usernames(response)
    and USER_OTHER not in usernames(response)
)
results.append(("search 'alex' returns matching users, excludes self", ok, str(sorted(usernames(response)))))

# --- 1b. The caller never sees itself -------------------------------------------
response = client.get("/users/search", params={"q": RUN_ID}, headers=AUTH)
ok = (
    response.status_code == 200
    and USER_A not in usernames(response)
    and {USER_B, USER_OTHER} <= usernames(response)
)
results.append(("caller is excluded from own results", ok, str(sorted(usernames(response)))))

# --- 2. Case-insensitive ------------------------------------------------------
response = client.get("/users/search", params={"q": "ALEX"}, headers=AUTH)
ok = response.status_code == 200 and USER_B in usernames(response) and USER_A not in usernames(response)
results.append(("search 'ALEX' is case-insensitive", ok, str(sorted(usernames(response)))))

# --- 3. Mid-string partial match -----------------------------------------------
response = client.get("/users/search", params={"q": RUN_ID}, headers=AUTH)
ok = response.status_code == 200 and {USER_B, USER_OTHER} <= usernames(response)
results.append(("mid-string partial match finds users", ok, str(sorted(usernames(response)))))

# --- 4. No matches -> empty list -------------------------------------------------
response = client.get("/users/search", params={"q": "zzzz_nobody_here"}, headers=AUTH)
ok = response.status_code == 200 and response.json() == []
results.append(("no matches returns empty list", ok, str(response.json())))

# --- 5. Missing token -> 401 -----------------------------------------------------
response = client.get("/users/search", params={"q": "alex"})
results.append(("no token -> 401", response.status_code == 401, str(response.status_code)))

# --- 6. Invalid / expired token -> 401 -------------------------------------------
response = client.get("/users/search", params={"q": "alex"}, headers={"Authorization": "Bearer not.a.jwt"})
results.append(("invalid token -> 401", response.status_code == 401, str(response.status_code)))

expired = make_token(str(uuid.uuid4()), timedelta(minutes=-5))
response = client.get("/users/search", params={"q": "alex"}, headers={"Authorization": f"Bearer {expired}"})
results.append(("expired token -> 401", response.status_code == 401, str(response.status_code)))

# --- 7. Inactive users excluded ---------------------------------------------------
response = client.get("/users/search", params={"q": "alex_inactive"}, headers=AUTH)
ok = response.status_code == 200 and USER_INACTIVE not in usernames(response)
results.append(("inactive user excluded from results", ok, str(sorted(usernames(response)))))

# --- 8. Empty / invalid query -> 422 -----------------------------------------------
for label, params in [
    ("missing q", {}),
    ("empty q", {"q": ""}),
    ("q too long", {"q": "a" * 31}),
]:
    response = client.get("/users/search", params=params, headers=AUTH)
    results.append((f"invalid query -> 422 ({label})", response.status_code == 422, str(response.status_code)))

# --- 9. limit validation ------------------------------------------------------------
for label, params in [
    ("limit 0", {"q": "alex", "limit": 0}),
    ("limit 21", {"q": "alex", "limit": 21}),
]:
    response = client.get("/users/search", params=params, headers=AUTH)
    results.append((f"invalid limit -> 422 ({label})", response.status_code == 422, str(response.status_code)))

response = client.get("/users/search", params={"q": "alex", "limit": 1}, headers=AUTH)
ok = response.status_code == 200 and len(response.json()) == 1
results.append(("limit=1 caps results", ok, str(len(response.json()))))

# --- 10. Wildcard characters in q are treated literally ------------------------------
response = client.get("/users/search", params={"q": "%"}, headers=AUTH)
ok = response.status_code == 200 and response.json() == []
results.append(("'%' in q matches nothing (wildcards escaped)", ok, str(response.json())))

# --- 11. Response exposes ONLY public fields -------------------------------------------
PUBLIC_FIELDS = {"user_id", "username", "profile_picture_url", "biography"}
SENSITIVE = {"email", "password", "password_hash", "coin_balance", "winning_streak", "is_active", "created_at"}
response = client.get("/users/search", params={"q": "alex"}, headers=AUTH)
body = response.json()
ok = (len(body) > 0 and set(body[0]) == PUBLIC_FIELDS and all(k not in body[0] for k in SENSITIVE))
results.append(("response has only public fields", ok, str(sorted(set(body[0]).intersection(SENSITIVE)))))

try:
    failed = 0
    for name, ok, extra in results:
        report(name, ok, extra)
        failed += 0 if ok else 1
    print(f"\n{len(results) - failed}/{len(results)} checks passed.")
    if failed:
        raise SystemExit(1)
finally:
    cleanup()