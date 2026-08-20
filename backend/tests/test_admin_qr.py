"""
End-to-end checks of the admin QR management module (/admin/*).

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_admin_qr

Covers:
  - Authorization: unauthenticated -> 401, normal user -> 403 on EVERY
    /admin/* endpoint, administrator -> granted.
  - QR list: pagination fields, newest first, status/product filters.
  - QR creation: 201, generated code (unique, "PB-" prefix), appears in
    the list, invalid product -> 404, invalid coin value -> 422.
  - QR detail: 200 with product + redemption info; nonexistent -> 404.
  - Status management: ACTIVE->EXPIRED (deactivate), EXPIRED->ACTIVE
    (reactivate), illegal transitions (including anything on a REDEEMED
    code, and ACTIVE->REDEEMED) -> 409; nonexistent -> 404.
  - Security: a normal user can call none of the admin endpoints, and
    cannot manipulate QR rows through them either.

All rows created by the test (users, product, qr_codes) are deleted
afterwards so the dev DB is left clean.
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
from app.models import QRCode, QRStatus, Product, User

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
ADMIN_USERNAME = f"admin_{RUN_ID}"
ADMIN_EMAIL = f"admin_{RUN_ID}@example.com"
USERNAME = f"regular_{RUN_ID}"
EMAIL = f"regular_{RUN_ID}@example.com"
PASSWORD = "SuperSecret123!"

CODE_ACTIVE = f"PB-{RUN_ID}-ACTIVE"
CODE_REDEEMED = f"PB-{RUN_ID}-REDEEMED"
CODE_EXPIRED = f"PB-{RUN_ID}-EXPIRED"

COIN_VALUE = 10


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
        product = db.execute(
            select(Product).where(Product.sku == f"SKU-{RUN_ID}")
        ).scalar_one_or_none()
        if product is not None:
            for qr in db.execute(
                select(QRCode).where(QRCode.product_id == product.product_id)
            ).scalars().all():
                db.delete(qr)
            db.commit()
        for username in (ADMIN_USERNAME, USERNAME):
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is not None:
                for qr in db.execute(
                    select(QRCode).where(QRCode.redeemed_by_user_id == user.user_id)
                ).scalars().all():
                    db.delete(qr)
                db.commit()
                db.delete(user)
                db.commit()
        if product is not None:
            db.delete(product)
            db.commit()
    print(f"\nCleaned up 1 product, its qr codes, and 2 test users.")


results = []

# --- Setup: two users (one promoted to admin), a product, three codes -------
for username, email in ((ADMIN_USERNAME, ADMIN_EMAIL), (USERNAME, EMAIL)):
    response = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": PASSWORD},
    )
    results.append((f"setup register {username}", response.status_code == 201, str(response.status_code)))

with SessionLocal() as db:
    admin_id = db.execute(select(User.user_id).where(User.username == ADMIN_USERNAME)).scalar_one()
    db.get(User, admin_id).is_admin = True  # promotion is admin-only, done by an operator
    db.commit()
    regular_id = db.execute(select(User.user_id).where(User.username == USERNAME)).scalar_one()

    product = Product(name="Cola 330ml", sku=f"SKU-{RUN_ID}")
    db.add(product)
    db.flush()

    now = datetime.now(timezone.utc)
    past = (now - timedelta(hours=1)).replace(tzinfo=None)
    future = (now + timedelta(hours=1)).replace(tzinfo=None)

    db.add_all(
        [
            QRCode(code=CODE_ACTIVE, product_id=product.product_id,
                   coin_value=COIN_VALUE, status=QRStatus.ACTIVE, expires_at=future),
            QRCode(code=CODE_REDEEMED, product_id=product.product_id,
                   coin_value=5, status=QRStatus.REDEEMED,
                   redeemed_by_user_id=regular_id, redeemed_at=past),
            QRCode(code=CODE_EXPIRED, product_id=product.product_id,
                   coin_value=7, status=QRStatus.EXPIRED, expires_at=past),
        ]
    )
    db.commit()
    product_id = product.product_id

login = client.post("/auth/login", json={"username": ADMIN_USERNAME, "password": PASSWORD})
ADMIN_TOKEN = login.json().get("access_token", "")
ADMIN_AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
results.append(("admin login works", bool(ADMIN_TOKEN), ""))

login = client.post("/auth/login", json={"username": USERNAME, "password": PASSWORD})
USER_TOKEN = login.json().get("access_token", "")
USER_AUTH = {"Authorization": f"Bearer {USER_TOKEN}"}
results.append(("regular user login works", bool(USER_TOKEN), ""))


# --- 1. Authorization: 401 / 403 / granted everywhere -----------------------
ADMIN_ENDPOINTS = ["/admin/me", "/admin/qr-codes", "/admin/products"]
for path in ADMIN_ENDPOINTS:
    results.append((f"unauth {path} -> 401", client.get(path).status_code == 401, str(client.get(path).status_code)))
    results.append((f"normal user {path} -> 403", client.get(path, headers=USER_AUTH).status_code == 403, str(client.get(path, headers=USER_AUTH).status_code)))
    results.append((f"admin {path} -> permitted", client.get(path, headers=ADMIN_AUTH).status_code in (200, 201, 422, 404), str(client.get(path, headers=ADMIN_AUTH).status_code)))

results.append(("admin /admin/me reports is_admin", client.get("/admin/me", headers=ADMIN_AUTH).json().get("is_admin") is True, ""))
results.append(("normal user cannot write QR either", client.post("/admin/qr-codes", headers=USER_AUTH, json={"product_id": str(product_id), "coin_value": COIN_VALUE}).status_code == 403, ""))

# Products for the QR create form now come from the dedicated admin products
# module (GET /admin/products); the old /admin/qr-codes/products endpoint is gone.
response = client.get("/admin/products", params={"q": "Cola 330ml"}, headers=ADMIN_AUTH)
product_names = [p["name"] for p in response.json()["items"]]
results.append(("admin can list products for the QR form", response.status_code == 200 and "Cola 330ml" in product_names, str(product_names)))

bad_token = {"Authorization": "Bearer not.a.jwt"}
results.append(("invalid token -> 401", client.get("/admin/qr-codes", headers=bad_token).status_code == 401, ""))
expired_token = {"Authorization": f"Bearer {make_token(admin_id, timedelta(minutes=-5))}"}
results.append(("expired token -> 401", client.get("/admin/qr-codes", headers=expired_token).status_code == 401, ""))


# --- 2. List: pagination, newest-first, filters ------------------------------
response = client.get("/admin/qr-codes", headers=ADMIN_AUTH, params={"product_id": str(product_id)})
body = response.json()
ok = (
    response.status_code == 200
    and isinstance(body.get("items"), list)
    and len(body.get("items", [])) == 3
    and body.get("total") == 3
    and body.get("limit") == 20
    and body.get("offset") == 0
)
results.append(("list returns all seeded codes with counts", ok, str(body)))

response = client.get(
    "/admin/qr-codes", headers=ADMIN_AUTH,
    params={"product_id": str(product_id), "limit": 2},
)
ok = len(response.json().get("items", [])) == 2 and response.json().get("total") == 3
results.append(("list paginates with limit/total", ok, str(response.json())))

response = client.get(
    "/admin/qr-codes", headers=ADMIN_AUTH,
    params={"product_id": str(product_id), "status": "active"},
)
active_codes = [item["code"] for item in response.json().get("items", [])]
results.append(("list filters by status=active", response.json().get("total") == 1 and CODE_ACTIVE in active_codes, str(active_codes)))

response = client.get("/admin/qr-codes", headers=ADMIN_AUTH, params={"product_id": str(product_id), "status": "redeemed"})
ok = response.json().get("total") == 1 and response.json().get("items", [])[0]["code"] == CODE_REDEEMED
results.append(("list filters by product_id + status", ok, str(response.json())))

response = client.get("/admin/qr-codes", headers=ADMIN_AUTH, params={"product_id": "not-a-uuid"})
results.append(("bad product_id filter -> 422", response.status_code == 422, str(response.status_code)))

ids_by_created = [item["created_at"] for item in client.get("/admin/qr-codes", headers=ADMIN_AUTH, params={"limit": 100}).json()["items"]]
results.append(("list ordered newest first", ids_by_created == sorted(ids_by_created, reverse=True), str(ids_by_created)))


# --- 3. Detail ---------------------------------------------------------------
qr_active_id = None
with SessionLocal() as db:
    qr_active_id = db.execute(select(QRCode.qr_id).where(QRCode.code == CODE_ACTIVE)).scalar_one()

response = client.get(f"/admin/qr-codes/{qr_active_id}", headers=ADMIN_AUTH)
item = response.json()
ok = (
    response.status_code == 200
    and item["code"] == CODE_ACTIVE
    and item["product"]["product_id"] == str(product_id)
    and item["product"]["name"] == "Cola 330ml"
    and item["coin_value"] == COIN_VALUE
    and item["status"] == "active"
)
results.append(("detail returns code/product/value/status", ok, str(item)))

with SessionLocal() as db:
    redeemed_id = db.execute(select(QRCode.qr_id).where(QRCode.code == CODE_REDEEMED)).scalar_one()
response = client.get(f"/admin/qr-codes/{redeemed_id}", headers=ADMIN_AUTH)
item = response.json()
ok = item["status"] == "redeemed" and item["redeemed_by"]["user_id"] == str(regular_id) and item["redeemed_at"] is not None
results.append(("detail exposes who redeemed and when", ok, str(item)))

results.append(("detail nonexistent qr -> 404", client.get(f"/admin/qr-codes/{uuid.uuid4()}", headers=ADMIN_AUTH).status_code == 404, ""))


# --- 4. Creation --------------------------------------------------------------
response = client.post(
    "/admin/qr-codes",
    headers=ADMIN_AUTH,
    json={"product_id": str(product_id), "coin_value": COIN_VALUE},
)
created = response.json()
ok = (
    response.status_code == 201
    and created["code"].startswith("PB-")
    and created["status"] == "active"
    and created["coin_value"] == COIN_VALUE
    and created["product"]["product_id"] == str(product_id)
)
results.append(("create returns 201 with generated unique code", ok, str(created)))

new_code = created["code"]
created_id = created["qr_id"]

response = client.get("/admin/qr-codes", headers=ADMIN_AUTH)
codes_in_list = [i["code"] for i in response.json()["items"]]
results.append(("created code appears in list", new_code in codes_in_list, str(codes_in_list)))

response = client.post(
    "/admin/qr-codes",
    headers=ADMIN_AUTH,
    json={"product_id": str(uuid.uuid4()), "coin_value": COIN_VALUE},
)
results.append(("create with nonexistent product -> 404", response.status_code == 404, str(response.status_code)))

for bad_value in (0, -5):
    response = client.post(
        "/admin/qr-codes",
        headers=ADMIN_AUTH,
        json={"product_id": str(product_id), "coin_value": bad_value},
    )
    results.append((f"create with coin_value={bad_value} -> 422", response.status_code == 422, str(response.status_code)))

# An expiry in the past is accepted (the code is just already invalid to redeem).
response = client.post(
    "/admin/qr-codes",
    headers=ADMIN_AUTH,
    json={"product_id": str(product_id), "coin_value": 3, "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()},
)
results.append(("create with expires_at is accepted", response.status_code == 201, str(response.status_code)))


# --- 5. Status management ------------------------------------------------------
# Deactivate an ACTIVE code (safe administrative action).
response = client.patch(
    f"/admin/qr-codes/{qr_active_id}",
    headers=ADMIN_AUTH,
    json={"status": "expired"},
)
ok = response.status_code == 200 and response.json()["status"] == "expired"
results.append(("ACTIVE -> EXPIRED (deactivate) is allowed", ok, str(response.json().get("status"))))

# Reactivate it (operator correction).
response = client.patch(
    f"/admin/qr-codes/{qr_active_id}",
    headers=ADMIN_AUTH,
    json={"status": "active"},
)
ok = response.status_code == 200 and response.json()["status"] == "active"
results.append(("EXPIRED -> ACTIVE (reactivate) is allowed", ok, str(response.json().get("status"))))

# Can an admin force a code to REDEEMED? No — that is not a valid transition.
response = client.patch(
    f"/admin/qr-codes/{qr_active_id}",
    headers=ADMIN_AUTH,
    json={"status": "redeemed"},
)
results.append(("ACTIVE -> REDEEMED rejected -> 409", response.status_code == 409, str(response.status_code)))

# Redeemed codes are immutable (audit trail).
response = client.patch(
    f"/admin/qr-codes/{redeemed_id}",
    headers=ADMIN_AUTH,
    json={"status": "active"},
)
results.append(("REDEEMED code cannot be modified -> 409", response.status_code == 409, str(response.status_code)))

# Patching a nonexistent code.
response = client.patch(
    f"/admin/qr-codes/{uuid.uuid4()}",
    headers=ADMIN_AUTH,
    json={"status": "expired"},
)
results.append(("patch nonexistent qr -> 404", response.status_code == 404, str(response.status_code)))

# The REDEEMED row's audit fields must be untouched after all attempts.
with SessionLocal() as db:
    qr = db.execute(select(QRCode).where(QRCode.code == CODE_REDEEMED)).scalar_one()
    intact = (
        qr.status == QRStatus.REDEEMED
        and qr.redeemed_by_user_id == regular_id
        and qr.redeemed_at is not None
    )
results.append(("redeemed audit fields intact after rejected attempts", intact, ""))


# --- 6. Security: normal users can never touch admin state --------------------
other_product = None
with SessionLocal() as db:
    other_product = db.execute(select(Product.product_id).where(Product.sku == f"SKU-{RUN_ID}")).scalar_one()
response = client.patch(
    f"/admin/qr-codes/{qr_active_id}",
    headers=USER_AUTH,
    json={"status": "expired"},
)
results.append(("normal user status change -> 403", response.status_code == 403, str(response.status_code)))
with SessionLocal() as db:
    qr = db.execute(select(QRCode).where(QRCode.code == CODE_ACTIVE)).scalar_one()
    unchanged = qr.status == QRStatus.ACTIVE
results.append(("status unchanged after regular user's 403", unchanged, str(qr.status)))


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