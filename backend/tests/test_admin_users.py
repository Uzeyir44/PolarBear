"""
End-to-end checks of the admin user-management module (/admin/users).

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_admin_users

Covers:
  - Authorization: unauthenticated -> 401 and normal users -> 403 on
    every /admin/users endpoint; an administrator is granted access.
  - Listing: newest-first ordering, pagination (limit/offset/total),
    username and email search (reusing the public ILIKE escaping), and
    the is_active filter.
  - Detail: safe administrative fields, no password_hash or other
    sensitive authentication data, 404 for nonexistent users.
  - Status management: deactivate -> the user can no longer log in,
    reactivate -> they can again; self-deactivation refused; nonexistent
    user -> 404; non-admin attempts -> 403.

All users created by the test are deleted afterwards so the dev DB is
left clean.
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
ADMIN_USERNAME = f"uadmin_{RUN_ID}"
ADMIN_EMAIL = f"uadmin_{RUN_ID}@example.com"
USERNAMES = [f"uuser{i}_{RUN_ID}" for i in range(1, 4)]
PASSWORD = "SuperSecret123!"

# Whitelist of every field the admin API may expose for a user.
SAFE_FIELDS = {
    "user_id", "username", "email", "profile_picture_url", "biography",
    "coin_balance", "winning_streak", "is_active", "created_at", "updated_at",
}
SENSITIVE_KEYS = {"password", "password_hash", "auth_providers", "provider", "token", "access_token"}


def report(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({extra})" if extra else ""))


def make_token(user_id: str, expires_delta: timedelta | None = None) -> str:
    expires_delta = expires_delta or timedelta(minutes=5)
    payload = {
        "sub": str(user_id),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + expires_delta,
    }
    return pyjwt.encode(payload, settings.secret_key, algorithm="HS256")


def cleanup() -> None:
    with SessionLocal() as db:
        for username in [ADMIN_USERNAME, *USERNAMES]:
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is not None:
                db.delete(user)
        db.commit()
    print(f"\nCleaned up 1 admin and 3 test users.")


results = []

# --- Setup: an admin (promoted via SQL, as an operator would) + 3 users -----
response = client.post(
    "/auth/register",
    json={"username": ADMIN_USERNAME, "email": ADMIN_EMAIL, "password": PASSWORD},
)
results.append(("setup register admin", response.status_code == 201, str(response.status_code)))

with SessionLocal() as db:
    admin_id = db.execute(select(User.user_id).where(User.username == ADMIN_USERNAME)).scalar_one()
    db.get(User, admin_id).is_admin = True
    db.commit()

for i, username in enumerate(USERNAMES, start=1):
    response = client.post(
        "/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
    )
    results.append((f"setup register {username}", response.status_code == 201, str(response.status_code)))

login = client.post("/auth/login", json={"username": ADMIN_USERNAME, "password": PASSWORD})
ADMIN_TOKEN = login.json().get("access_token", "")
ADMIN_AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
results.append(("admin login works", bool(ADMIN_TOKEN), ""))

login = client.post("/auth/login", json={"username": USERNAMES[0], "password": PASSWORD})
USER_TOKEN = login.json().get("access_token", "")
USER_AUTH = {"Authorization": f"Bearer {USER_TOKEN}"}
results.append(("non-admin login works", bool(USER_TOKEN), ""))


# --- 1. Authorization: 401 / 403 / granted ------------------------------------
for path in ["/admin/users", f"/admin/users/{uuid.uuid4()}"]:
    results.append((f"unauth {path} -> 401", client.get(path).status_code == 401, str(client.get(path).status_code)))
    results.append((f"normal user GET {path} -> 403", client.get(path, headers=USER_AUTH).status_code == 403, str(client.get(path, headers=USER_AUTH).status_code)))

status_path = f"/admin/users/{uuid.uuid4()}/status"
results.append(("unauth POST status path -> 401", client.patch(status_path, json={"is_active": False}).status_code == 401, str(client.patch(status_path, json={"is_active": False}).status_code)))
results.append(("normal user PATCH status path -> 403", client.patch(status_path, headers=USER_AUTH, json={"is_active": False}).status_code == 403, str(client.patch(status_path, headers=USER_AUTH, json={"is_active": False}).status_code)))

results.append(("list as admin -> 200", client.get("/admin/users", headers=ADMIN_AUTH).status_code == 200, ""))

bad_token = {"Authorization": "Bearer not.a.jwt"}
results.append(("invalid token list -> 401", client.get("/admin/users", headers=bad_token).status_code == 401, ""))
expired_token = {"Authorization": f"Bearer {make_token(admin_id, timedelta(minutes=-5))}"}
results.append(("expired token list -> 401", client.get("/admin/users", headers=expired_token).status_code == 401, ""))


# --- 2. List: newest-first, pagination, search, is_active filter ---------------
# Every test username contains RUN_ID, so `q=RUN_ID` scopes the list to
# exactly this run's users regardless of what else is in the dev DB.
response = client.get("/admin/users", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 100})
body = response.json()
usernames = [u["username"] for u in body["items"]]
results.append(("list returns all 4 run users via search", body["total"] == 4 and all(u in usernames for u in [*USERNAMES, ADMIN_USERNAME]), f"total={body['total']}"))

# Relative newest-first ordering of our users (they were registered one per
# request, so created_at is strictly increasing regardless of other data).
index = {name: usernames.index(name) for name in [USERNAMES[2], USERNAMES[1], USERNAMES[0], ADMIN_USERNAME]}
ordered = index[USERNAMES[2]] < index[USERNAMES[1]] < index[USERNAMES[0]] < index[ADMIN_USERNAME]
results.append(("list is newest-first (relative order of test users)", ordered, str(index)))

# No sensitive fields anywhere in the page.
offenders = {k for item in body["items"] for k in item.keys()} & SENSITIVE_KEYS
results.append(("no password/session keys in list responses", not offenders, str(sorted(offenders)) or "clean"))

response = client.get("/admin/users", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 2})
results.append(("pagination limit=2 -> 2 items, total=4", len(response.json()["items"]) == 2 and response.json()["total"] == 4, str(response.json())))

response = client.get("/admin/users", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 2, "offset": 2})
p2 = response.json()
page1 = client.get("/admin/users", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 2, "offset": 0}).json()["items"]
page1_ids = {u["user_id"] for u in page1}
page2_ids = {u["user_id"] for u in p2["items"]}
results.append(("pagination offset=2 continues without overlap", len(p2["items"]) == 2 and p2["total"] == 4 and not (page1_ids & page2_ids), f"total={p2['total']}"))

response = client.get("/admin/users", headers=ADMIN_AUTH, params={"q": f"uuser2_{RUN_ID}"})
names = [u["username"] for u in response.json()["items"]]
results.append(("search by username fragment works", response.json()["total"] == 1 and f"uuser2_{RUN_ID}" in names, str(names)))

response = client.get("/admin/users", headers=ADMIN_AUTH, params={"q": f"UUSER2_{RUN_ID}"})
results.append(("search is case-insensitive", response.json()["total"] == 1, str(response.json()["total"])))

response = client.get("/admin/users", headers=ADMIN_AUTH, params={"q": f"uuser2_{RUN_ID}@example.com"})
results.append(("search by email fragment works", response.json()["total"] == 1, str([u["email"] for u in response.json()["items"]])))

response = client.get("/admin/users", headers=ADMIN_AUTH, params={"q": "no-such-user-xyzzy"})
results.append(("search with no match -> empty", response.json()["total"] == 0 and response.json()["items"] == [], str(response.json()["total"])))

# A reserved-character search is treated literally (escape_like reuse).
response = client.get("/admin/users", headers=ADMIN_AUTH, params={"q": "50%_wildcard"})
results.append(("search escapes ILIKE wildcards", response.json()["total"] == 0, str(response.json()["total"])))


# --- 3. Detail ------------------------------------------------------------------
with SessionLocal() as db:
    first_id = db.execute(select(User.user_id).where(User.username == USERNAMES[0])).scalar_one()

response = client.get(f"/admin/users/{first_id}", headers=ADMIN_AUTH)
item = response.json()
results.append(("detail returns 200 with username", response.status_code == 200 and item["username"] == USERNAMES[0], str(response.status_code)))
results.append(("detail exposes safe admin fields", SAFE_FIELDS.issubset(item.keys()), str(sorted(item.keys()))))
results.append(("detail hides password/session data", not (set(item.keys()) & SENSITIVE_KEYS), str(sorted(set(item.keys()) & SENSITIVE_KEYS)) or "clean"))

results.append(("detail nonexistent user -> 404", client.get(f"/admin/users/{uuid.uuid4()}", headers=ADMIN_AUTH).status_code == 404, ""))
results.append(("status nonexistent user -> 404", client.patch(f"/admin/users/{uuid.uuid4()}/status", headers=ADMIN_AUTH, json={"is_active": False}).status_code == 404, ""))


# --- 4. Deactivate / reactivate --------------------------------------------------
with SessionLocal() as db:
    target_id = db.execute(select(User.user_id).where(User.username == USERNAMES[1])).scalar_one()

response = client.patch(f"/admin/users/{target_id}/status", headers=ADMIN_AUTH, json={"is_active": False})
ok = response.status_code == 200 and response.json()["is_active"] is False and response.json()["username"] == USERNAMES[1]
results.append(("deactivate -> 200, is_active false", ok, str(response.json()["is_active"])))

# A deactivated user is immediately locked out of protected endpoints.
response = client.post("/auth/login", json={"username": USERNAMES[1], "password": PASSWORD})
old_token = response.json().get("access_token", "")
results.append(("deactivated user cannot log in -> 401", client.get("/users/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401, ""))
with SessionLocal() as db:
    db_user = db.get(User, target_id)
    unchanged = db_user.is_active is False and db_user.username == USERNAMES[1] and db_user.password_hash is not None
results.append(("row updated, password untouched, user not deleted", unchanged, ""))

response = client.get("/admin/users", headers=ADMIN_AUTH, params={"q": RUN_ID, "is_active": "false"})
inactive_names = [u["username"] for u in response.json()["items"]]
results.append(("is_active=false filter shows the deactivated user", USERNAMES[1] in inactive_names, str(inactive_names)))

response = client.get("/admin/users", headers=ADMIN_AUTH, params={"q": RUN_ID, "is_active": "true"})
active_names = [u["username"] for u in response.json()["items"]]
results.append(("is_active=true filter excludes the deactivated user", USERNAMES[1] not in active_names, f"total={response.json()['total']}"))

# Reactivate.
response = client.patch(f"/admin/users/{target_id}/status", headers=ADMIN_AUTH, json={"is_active": True})
ok = response.status_code == 200 and response.json()["is_active"] is True
results.append(("reactivate -> 200, is_active true", ok, str(response.json()["is_active"])))

# Back to normal.
response = client.post("/auth/login", json={"username": USERNAMES[1], "password": PASSWORD})
re_token = response.json().get("access_token", "")
results.append(("reactivated user can log in again", client.get("/users/me", headers={"Authorization": f"Bearer {re_token}"}).status_code == 200, ""))


# --- 5. Security guards -----------------------------------------------------------
response = client.patch(f"/admin/users/{first_id}/status", headers=USER_AUTH, json={"is_active": False})
results.append(("normal user status change -> 403", response.status_code == 403, str(response.status_code)))

with SessionLocal() as db:
    untouched = db.get(User, first_id).is_active is True
results.append(("user status unchanged after non-admin 403", untouched, ""))

response = client.patch(f"/admin/users/{admin_id}/status", headers=ADMIN_AUTH, json={"is_active": False})
results.append(("admin cannot deactivate themselves -> 400", response.status_code == 400, str(response.status_code)))
with SessionLocal() as db:
    still_admin = db.get(User, admin_id).is_active is True
results.append(("self-deactivation refused in the DB as well", still_admin, ""))


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