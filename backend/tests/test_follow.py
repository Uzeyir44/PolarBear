"""
End-to-end check of the follow feature — POST /users/{id}/follow,
DELETE /users/{id}/follow, GET /users/{id}/follow-status.

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_follow

Covers: valid follow, duplicate follow -> 409, self-follow -> 400,
nonexistent target -> 404, inactive target -> 400, unfollow -> 200,
unfollow someone not followed -> 404, follow-status true/false,
missing/invalid token -> 401, and that a body-supplied follower_id is
ignored (the follower always comes from the JWT). Test users and any
follow rows are deleted afterwards so the development DB is left clean.
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
from app.models import Follow, User

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

USER_FOLLOWER = f"flw_follow_{RUN_ID}"
USER_FOLLOWEE = f"flw_teed_{RUN_ID}"
USER_OTHER = f"flw_other_{RUN_ID}"
USER_INACTIVE = f"flw_inact_{RUN_ID}"

USERS = [
    (USER_FOLLOWER, f"{USER_FOLLOWER}@example.com"),
    (USER_FOLLOWEE, f"{USER_FOLLOWEE}@example.com"),
    (USER_OTHER, f"{USER_OTHER}@example.com"),
    (USER_INACTIVE, f"{USER_INACTIVE}@example.com"),
]

NONEXISTENT_ID = str(uuid.uuid4())


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


def user_id_of(db, username: str) -> str:
    return str(db.execute(select(User.user_id).where(User.username == username)).scalar_one())


def count_follows(follower_id: str, followee_id: str) -> int:
    with SessionLocal() as db:
        return db.execute(
            select(Follow).where(
                Follow.follower_id == follower_id, Follow.followee_id == followee_id
            )
        ).scalars().all().__len__()


results = []

# --- Setup: register four users and log in as the follower ------------------
for username, email in USERS:
    response = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": PASSWORD},
    )
    results.append((f"setup register {username}", response.status_code == 201, str(response.status_code)))

# Deactivate the "inactive" user directly in the DB (as other tests do).
with SessionLocal() as db:
    user_inactive = db.execute(
        select(User).where(User.username == USER_INACTIVE)
    ).scalar_one()
    user_inactive.is_active = False
    db.commit()

with SessionLocal() as db:
    FOLLOWER_ID = user_id_of(db, USER_FOLLOWER)
    FOLLOWEE_ID = user_id_of(db, USER_FOLLOWEE)
    OTHER_ID = user_id_of(db, USER_OTHER)
    INACTIVE_ID = user_id_of(db, USER_INACTIVE)

response = client.post("/auth/login", json={"username": USER_FOLLOWER, "password": PASSWORD})
token = response.json().get("access_token", "")
AUTH = {"Authorization": f"Bearer {token}"}
results.append(("setup login works", bool(token), ""))


# --- 1. Missing token -> 401 ---------------------------------------------------
response = client.post(f"/users/{FOLLOWEE_ID}/follow")
results.append(("follow without token -> 401", response.status_code == 401, str(response.status_code)))

response = client.delete(f"/users/{FOLLOWEE_ID}/follow")
results.append(("unfollow without token -> 401", response.status_code == 401, str(response.status_code)))

response = client.get(f"/users/{FOLLOWEE_ID}/follow-status")
results.append(("follow-status without token -> 401", response.status_code == 401, str(response.status_code)))

# --- 2. Invalid token -> 401 ------------------------------------------------------
response = client.post(
    f"/users/{FOLLOWEE_ID}/follow", headers={"Authorization": "Bearer not.a.jwt"}
)
results.append(("follow with invalid token -> 401", response.status_code == 401, str(response.status_code)))

expired = make_token(str(uuid.uuid4()), timedelta(minutes=-5))
response = client.post(
    f"/users/{FOLLOWEE_ID}/follow", headers={"Authorization": f"Bearer {expired}"}
)
results.append(("follow with expired token -> 401", response.status_code == 401, str(response.status_code)))

# --- 3. Follow nonexistent user -> 404 --------------------------------------------
response = client.post(f"/users/{NONEXISTENT_ID}/follow", headers=AUTH)
results.append(("follow nonexistent user -> 404", response.status_code == 404, str(response.status_code)))

# --- 4. Follow inactive user -> 400 -------------------------------------------------
response = client.post(f"/users/{INACTIVE_ID}/follow", headers=AUTH)
results.append(("follow inactive user -> 400", response.status_code == 400, str(response.status_code)))

# --- 5. Follow yourself -> 400 --------------------------------------------------------
response = client.post(f"/users/{FOLLOWER_ID}/follow", headers=AUTH)
results.append(("follow yourself -> 400", response.status_code == 400, str(response.status_code)))

# --- 6. Valid follow -> 201, row exists, is_following true -----------------------------
response = client.post(f"/users/{FOLLOWEE_ID}/follow", headers=AUTH)
ok = (
    response.status_code == 201
    and response.json() == {"is_following": True}
    and count_follows(FOLLOWER_ID, FOLLOWEE_ID) == 1
)
results.append(("valid follow -> 201 with is_following true + DB row", ok, str(response.status_code)))

# --- 7. Duplicate follow -> 409 -----------------------------------------------------------
response = client.post(f"/users/{FOLLOWEE_ID}/follow", headers=AUTH)
ok = response.status_code == 409 and count_follows(FOLLOWER_ID, FOLLOWEE_ID) == 1
results.append(("follow same user twice -> 409, no duplicate row", ok, str(response.status_code)))

# --- 8. A body-supplied follower_id is ignored (follower always = JWT user) --------------
# Send a body claiming OTHER_ID is the follower; the row must still belong to FOLLOWER_ID.
response = client.post(
    f"/users/{FOLLOWEE_ID}/follow",
    headers=AUTH,
    json={"follower_id": OTHER_ID},
)
ok = (
    response.status_code == 409  # still the existing follow, not a new one
    and count_follows(FOLLOWER_ID, FOLLOWEE_ID) == 1
    and count_follows(OTHER_ID, FOLLOWEE_ID) == 0
)
results.append(("body-supplied follower_id is ignored", ok, str(response.status_code)))

# --- 9. follow-status after following -> true ----------------------------------------------
response = client.get(f"/users/{FOLLOWEE_ID}/follow-status", headers=AUTH)
ok = response.status_code == 200 and response.json() == {"is_following": True}
results.append(("follow-status -> is_following true", ok, str(response.status_code)))

# --- 10. Unfollow someone not followed -> 404 --------------------------------------------------
response = client.delete(f"/users/{OTHER_ID}/follow", headers=AUTH)
results.append(("unfollow someone not followed -> 404", response.status_code == 404, str(response.status_code)))

# --- 11. Valid unfollow -> 200, row gone, is_following false -----------------------------------
response = client.delete(f"/users/{FOLLOWEE_ID}/follow", headers=AUTH)
ok = (
    response.status_code == 200
    and response.json() == {"is_following": False}
    and count_follows(FOLLOWER_ID, FOLLOWEE_ID) == 0
)
results.append(("valid unfollow -> 200 with is_following false + row deleted", ok, str(response.status_code)))

# --- 12. Unfollow again after deleting -> 404 ---------------------------------------------------
response = client.delete(f"/users/{FOLLOWEE_ID}/follow", headers=AUTH)
results.append(("unfollow again -> 404", response.status_code == 404, str(response.status_code)))

# --- 13. follow-status after unfollow -> false ---------------------------------------------------
response = client.get(f"/users/{FOLLOWEE_ID}/follow-status", headers=AUTH)
ok = response.status_code == 200 and response.json() == {"is_following": False}
results.append(("follow-status -> is_following false", ok, str(response.status_code)))

# --- 14. A fresh second user can still follow the same target -------------------------------------
response = client.post("/auth/login", json={"username": USER_OTHER, "password": PASSWORD})
other_token = response.json().get("access_token", "")
response = client.post(
    f"/users/{FOLLOWEE_ID}/follow", headers={"Authorization": f"Bearer {other_token}"}
)
ok = response.status_code == 201 and count_follows(OTHER_ID, FOLLOWEE_ID) == 1
results.append(("a different follower can follow the same target", ok, str(response.status_code)))
with SessionLocal() as db:
    other_follow = db.execute(
        select(Follow).where(Follow.follower_id == OTHER_ID, Follow.followee_id == FOLLOWEE_ID)
    ).scalar_one()
    db.delete(other_follow)
    db.commit()

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
