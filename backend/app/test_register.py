"""
End-to-end check of POST /auth/register — FastAPI -> SQLAlchemy -> PostgreSQL.

Run from the backend/ directory:
    venv/Scripts/python -m app.test_register

Uses unique test user records and deletes them afterwards so the
development database is left clean.
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
from app.core.security import password_hasher
from app.main import app
from app.models import User

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid4().hex[:6]}"
USERNAME = f"testuser_{RUN_ID}"
EMAIL = f"test_{RUN_ID}@example.com"
PASSWORD = "SuperSecret123!"

created_usernames = set()

PASS_ICON = "PASS"
FAIL_ICON = "FAIL"


def report(name: str, ok: bool, extra: str = "") -> None:
    status = PASS_ICON if ok else FAIL_ICON
    print(f"[{status}] {name}" + (f"  ({extra})" if extra else ""))


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

# 1. Successful registration
response = client.post(
    "/auth/register",
    json={"username": USERNAME, "email": EMAIL, "password": PASSWORD},
)
created_usernames.add(USERNAME)
ok = response.status_code == 201
results.append(("successful registration returns 201", ok, str(response.status_code)))
body = response.json()
results.append(
    ("response exposes no password or password_hash",
     "password" not in body and "password_hash" not in body,
     str(sorted(body.keys())))
)
results.append(
    ("response body has expected user fields",
     {"user_id", "username", "email", "is_active", "created_at"} <= set(body),
     str(sorted(body.keys())))
)
results.append(
    ("response email matches request",
     body.get("email").lower() == EMAIL.lower(),
     str(body.get("email"))),
)

# 2. Password stored as hash, not plain text
user = db_user_by_username(USERNAME)
hash_value = user.password_hash if user else None
ok = (
    hash_value is not None
    and hash_value != PASSWORD
    and hash_value.startswith("$argon2id$")
    and password_hasher.verify(PASSWORD, hash_value)
)
results.append(("password stored as Argon2 hash (verifiable, not plaintext)", ok, str(hash_value)))

# 3. Duplicate username rejected
response = client.post(
    "/auth/register",
    json={"username": USERNAME, "email": f"other_{RUN_ID}@example.com", "password": PASSWORD},
)
ok = response.status_code == 409 and "username" in response.json().get("detail", "").lower()
results.append(("duplicate username rejected with 409", ok, str(response.json())))

# 4. Duplicate email rejected (case-insensitive because of CITEXT)
response = client.post(
    "/auth/register",
    json={"username": f"other_{RUN_ID}", "email": EMAIL.upper(), "password": PASSWORD},
)
ok = response.status_code == 409 and "email" in response.json().get("detail", "").lower()
results.append(("duplicate email rejected with 409 (case-insensitive)", ok, str(response.json())))

# 5. Invalid data rejected by Pydantic
invalid_cases = [
    ("bad email",
     {"username": f"bad_{RUN_ID}", "email": "not-an-email", "password": PASSWORD}),
    ("short password",
     {"username": f"bad2_{RUN_ID}", "email": f"bad2_{RUN_ID}@example.com", "password": "short"}),
    ("too-short username",
     {"username": "ab", "email": f"bad3_{RUN_ID}@example.com", "password": PASSWORD}),
    ("disallowed username chars",
     {"username": "no spaces!", "email": f"bad4_{RUN_ID}@example.com", "password": PASSWORD}),
    ("missing email",
     {"username": f"bad5_{RUN_ID}", "password": PASSWORD}),
]
for label, payload in invalid_cases:
    response = client.post("/auth/register", json=payload)
    ok = response.status_code == 422
    results.append((f"invalid data rejected ({label})", ok, str(response.status_code)))

# 6. No user was created for the rejected payloads
with SessionLocal() as db:
    collision_counts = db.execute(
        select(User).where(User.username.like(f"bad%_{RUN_ID}") | User.username.like(f"other%_{RUN_ID}"))
    ).scalars().all()
results.append(
    ("rejected payloads created no users",
     len(list(collision_counts)) == 0,
     f"{len(list(collision_counts))} unexpected rows"),
)

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