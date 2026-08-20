"""
End-to-end check of PATCH /users/me — profile update for the logged-in user.

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_update_profile

Covers: single-field updates leave other fields unchanged, multi-field
update, clearing a field with null, duplicate username 409 (including
case-insensitive via CITEXT), reusing your own username, missing token 401,
invalid usernames 422, and no password_hash in the response. Test users are
deleted afterwards so the development database is left clean.
"""
import time
import warnings
from uuid import uuid4

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import User

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid4().hex[:6]}"
USERNAME = f"profu_{RUN_ID}"
OTHER_USERNAME = f"other_{RUN_ID}"
EMAIL = f"profu_{RUN_ID}@example.com"
OTHER_EMAIL = f"other_{RUN_ID}@example.com"
PASSWORD = "SuperSecret123!"

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


results = []

# --- Setup: register the two users -------------------------------------------
for username, email in ((USERNAME, EMAIL), (OTHER_USERNAME, OTHER_EMAIL)):
    response = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": PASSWORD},
    )
    created_usernames.add(username)
    results.append((f"setup register {username}", response.status_code == 201, str(response.status_code)))

response = client.post("/auth/login", json={"username": USERNAME, "password": PASSWORD})
token = response.json().get("access_token", "")
results.append(("setup login works", bool(token), ""))

other_response = client.post("/auth/login", json={"username": OTHER_USERNAME, "password": PASSWORD})
other_token = other_response.json().get("access_token", "")

AUTH = {"Authorization": f"Bearer {token}"}
OTHER_AUTH = {"Authorization": f"Bearer {other_token}"}

orig_email = EMAIL.lower()

# --- 1. No token -> 401 -------------------------------------------------------
response = client.patch("/users/me", json={"biography": "x"})
results.append(("PATCH without token -> 401", response.status_code == 401, str(response.status_code)))

# --- 2. Update username only ---------------------------------------------------
response = client.patch("/users/me", json={"username": "renamed_" + RUN_ID}, headers=AUTH)
body = response.json()
ok = response.status_code == 200 and body.get("username") == "renamed_" + RUN_ID
results.append(("update username only", ok, str(body.get("username"))))
renamed_username = body.get("username")

# --- 3. Other fields unchanged after single-field update ------------------------
ok = (
    body.get("email") == orig_email
    and body.get("biography") is None
    and body.get("profile_picture_url") is None
    and body.get("is_active") is True
    and body.get("created_at") is not None
)
results.append(("single-field update left other fields unchanged", ok, str(body)))

# --- 4. Update biography only ---------------------------------------------------
response = client.patch("/users/me", json={"biography": "My new biography"}, headers=AUTH)
body = response.json()
ok = (
    response.status_code == 200
    and body.get("biography") == "My new biography"
    and body.get("username") == renamed_username
)
results.append(("update biography only keeps username", ok, str(body.get("biography"))))

# --- 5. Update multiple fields ---------------------------------------------------
response = client.patch(
    "/users/me",
    json={
        "username": "prof_" + RUN_ID,
        "biography": "A longer bio",
        "profile_picture_url": "https://example.com/avatar.jpg",
    },
    headers=AUTH,
)
body = response.json()
ok = (
    response.status_code == 200
    and body.get("username") == "prof_" + RUN_ID
    and body.get("biography") == "A longer bio"
    and body.get("profile_picture_url") == "https://example.com/avatar.jpg"
)
results.append(("update multiple fields at once", ok, str(body)))
prof_username = body.get("username")

# --- 6. Clear a field by sending null -------------------------------------------
response = client.patch("/users/me", json={"biography": None}, headers=AUTH)
body = response.json()
ok = response.status_code == 200 and body.get("biography") is None
results.append(("null biography clears the field", ok, str(body.get("biography"))))

# --- 7. Response never exposes password_hash -------------------------------------
results.append(
    ("response has no password/password_hash",
     "password" not in body and "password_hash" not in body,
     str(sorted(body.keys()))),
)

# --- 8. Duplicate username -> 409 -------------------------------------------------
response = client.patch("/users/me", json={"username": prof_username}, headers=OTHER_AUTH)
ok = response.status_code == 409 and "username" in response.json().get("detail", "").lower()
results.append(("duplicate username rejected with 409", ok, str(response.json())))

# --- 9. Case-insensitive duplicate (CITEXT) -> 409 --------------------------------
response = client.patch("/users/me", json={"username": prof_username.upper()}, headers=OTHER_AUTH)
ok = response.status_code == 409 and "username" in response.json().get("detail", "").lower()
results.append(("case-insensitive duplicate username -> 409 (CITEXT)", ok, str(response.json())))

# --- 10. Reusing your own username is allowed --------------------------------------
response = client.patch("/users/me", json={"username": prof_username}, headers=AUTH)
ok = response.status_code == 200
results.append(("reusing own username allowed", ok, str(response.status_code)))

# --- 11. Invalid / empty usernames -> 422 -------------------------------------------
invalid_cases = [
    ("empty username", {"username": ""}),
    ("too-short username", {"username": "ab"}),
    ("disallowed chars", {"username": "bad name!"}),
    ("null username", {"username": None}),
]
for label, payload in invalid_cases:
    response = client.patch("/users/me", json=payload, headers=AUTH)
    results.append((f"invalid payload -> 422 ({label})", response.status_code == 422, str(response.status_code)))

# --- 12. Database reflects the final state ------------------------------------------
user = db_user_by_username(prof_username)
ok = (
    user is not None
    and user.biography is None
    and user.profile_picture_url == "https://example.com/avatar.jpg"
    and user.email == orig_email
)
results.append(("DB row matches final profile state", ok, str(user)))

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