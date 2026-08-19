"""
End-to-end check of POST /qr/redeem — Step 2: validate AND mark redeemed.

Run from the backend/ directory:
    venv/Scripts/python -m app.test_qr_redeem

Covers: valid active code -> 200 and the qr_codes row is actually updated
(status REDEEMED, redeemed_by_user_id = the caller, redeemed_at set),
the same code cannot be redeemed again by anyone -> 409, nonexistent code
-> 404, redeemed code -> 409, expired code -> 410, active-but-past-
expires_at -> 410, missing/invalid/expired token -> 401, the response
exposes only message/qr_id/redeemed_at, and redemption NEVER touches
coin_balance or coin_transactions (that's the next step). Test products,
qr_codes, and users are deleted afterwards so the dev DB is left clean.
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
from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import QRCode, QRStatus, Product, User

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
USERNAME = f"qruser_{RUN_ID}"
EMAIL = f"{USERNAME}@example.com"
USERNAME_2 = f"qrus2_{RUN_ID}"
EMAIL_2 = f"{USERNAME_2}@example.com"
PASSWORD = "SuperSecret123!"

CODE_VALID = f"COLA-{RUN_ID}-VALID"
CODE_REDEEMED = f"COLA-{RUN_ID}-REDEEMED"
CODE_EXPIRED = f"COLA-{RUN_ID}-EXPIRED"
CODE_OVERDUE = f"COLA-{RUN_ID}-OVERDUE"
CODE_NONEXISTENT = f"COLA-{RUN_ID}-NOPE"

VALID_COIN_VALUE = 10

created_codes = []


def report(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({extra})" if extra else ""))


def cleanup() -> None:
    with SessionLocal() as db:
        # Delete qr codes first: both product_id and redeemed_by_user_id are
        # RESTRICT FKs, so children must go before their parents.
        for code in created_codes:
            qr = db.execute(select(QRCode).where(QRCode.code == code)).scalar_one_or_none()
            if qr is not None:
                db.delete(qr)
        db.commit()
        for username in (USERNAME, USERNAME_2):
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is not None:
                db.delete(user)
        db.commit()
        product = db.execute(
            select(Product).where(Product.sku == f"SKU-{RUN_ID}")
        ).scalar_one_or_none()
        if product is not None:
            db.delete(product)
        db.commit()
    print(
        f"\nCleaned up {len(created_codes)} qr code(s), the product, and {2} test users."
    )


def make_token(user_id: str, expires_delta: timedelta) -> str:
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + expires_delta,
    }
    return pyjwt.encode(payload, settings.secret_key, algorithm="HS256")


results = []

# --- Setup: register two users, seed products and qr_codes directly ---
for username, email in ((USERNAME, EMAIL), (USERNAME_2, EMAIL_2)):
    response = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": PASSWORD},
    )
    results.append((f"setup register {username}", response.status_code == 201, str(response.status_code)))

with SessionLocal() as db:
    user_id = str(db.execute(select(User.user_id).where(User.username == USERNAME)).scalar_one())

    product = Product(name="Cola 330ml", sku=f"SKU-{RUN_ID}")
    db.add(product)
    db.flush()

    # Naive UTC datetimes: matches the `timestamp without time zone` columns
    # and the endpoint's "stored values are UTC" assumption.
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)

    db.add_all(
        [
            QRCode(code=CODE_VALID, product_id=product.product_id,
                   coin_value=VALID_COIN_VALUE, status=QRStatus.ACTIVE,
                   expires_at=future),
            QRCode(code=CODE_REDEEMED, product_id=product.product_id,
                   coin_value=5, status=QRStatus.REDEEMED,
                   redeemed_by_user_id=uuid.UUID(user_id), redeemed_at=past),
            QRCode(code=CODE_EXPIRED, product_id=product.product_id,
                   coin_value=5, status=QRStatus.EXPIRED, expires_at=past),
            QRCode(code=CODE_OVERDUE, product_id=product.product_id,
                   coin_value=7, status=QRStatus.ACTIVE, expires_at=past),
        ]
    )
    db.commit()

created_codes = [CODE_VALID, CODE_REDEEMED, CODE_EXPIRED, CODE_OVERDUE]

response = client.post("/auth/login", json={"username": USERNAME, "password": PASSWORD})
token = response.json().get("access_token", "")
AUTH = {"Authorization": f"Bearer {token}"}
results.append(("setup login works", bool(token), ""))

# Snapshot the user's coin balance and tx ledger BEFORE any redemption.
with SessionLocal() as db:
    coin_before = db.get(User, uuid.UUID(user_id)).coin_balance
    tx_before = db.execute(
        text("SELECT count(*) FROM coin_transactions WHERE user_id = :uid"), {"uid": user_id}
    ).scalar()


def redeem(code: str, headers: dict | None = None):
    return client.post("/qr/redeem", json={"code": code}, headers=headers)


# --- 1. Missing / invalid token -> 401 ----------------------------------------
results.append(("redeem without token -> 401", redeem(CODE_VALID).status_code == 401, ""))
results.append(
    (
        "redeem with invalid token -> 401",
        redeem(CODE_VALID, headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401,
        "",
    )
)
expired_token = make_token(str(uuid.uuid4()), timedelta(minutes=-5))
results.append(
    (
        "redeem with expired token -> 401",
        redeem(CODE_VALID, headers={"Authorization": f"Bearer {expired_token}"}).status_code == 401,
        "",
    )
)

# --- 2. Nonexistent code -> 404 --------------------------------------------------
response = redeem(CODE_NONEXISTENT, headers=AUTH)
results.append(("nonexistent code -> 404", response.status_code == 404, str(response.status_code)))

# --- 3. Pre-seeded redeemed code -> 409 ----------------------------------------------
response = redeem(CODE_REDEEMED, headers=AUTH)
results.append(("already redeemed code -> 409", response.status_code == 409, str(response.status_code)))

# --- 4. Expired code (status EXPIRED) -> 410 -------------------------------------------
response = redeem(CODE_EXPIRED, headers=AUTH)
results.append(("expired code -> 410", response.status_code == 410, str(response.status_code)))

# --- 5. Active code but past expires_at -> 410 -------------------------------------------
response = redeem(CODE_OVERDUE, headers=AUTH)
results.append(("active code past expires_at -> 410", response.status_code == 410, str(response.status_code)))

# --- 6. Valid active code -> 200 and the row is actually REDEEMED ---------------------------
response = redeem(CODE_VALID, headers=AUTH)
body = response.json()
ok_resp = (
    response.status_code == 200
    and body.get("message") == "Code redeemed successfully"
    and body.get("qr_id") is not None
    and body.get("redeemed_at") is not None
)
results.append(("valid code -> 200 with message/qr_id/redeemed_at", ok_resp, str(body)))

with SessionLocal() as db:
    qr = db.execute(select(QRCode).where(QRCode.code == CODE_VALID)).scalar_one()
    ok_db = (
        qr.status == QRStatus.REDEEMED
        and qr.redeemed_by_user_id == uuid.UUID(user_id)
        and qr.redeemed_at is not None
    )
results.append(
    ("DB row updated: status REDEEMED, redeemed_by = caller, redeemed_at set",
     ok_db, f"{qr.status}, {qr.redeemed_by_user_id}, {qr.redeemed_at}")
)

# --- 7. Response exposes ONLY the safe fields ------------------------------------------------
SAFE_FIELDS = {"message", "qr_id", "redeemed_at"}
INTERNAL = {"code", "product_id", "coin_value", "status", "redeemed_by_user_id", "expires_at", "created_at"}
ok = set(body) == SAFE_FIELDS and all(k not in body for k in INTERNAL)
results.append(("response exposes only message/qr_id/redeemed_at", ok, str(sorted(body.keys()))))

# --- 8. Same code by the same user again -> 409, no second redemption -------------------------
response = redeem(CODE_VALID, headers=AUTH)
ok = response.status_code == 409
with SessionLocal() as db:
    qr = db.execute(select(QRCode).where(QRCode.code == CODE_VALID)).scalar_one()
    still_ok = qr.status == QRStatus.REDEEMED and qr.redeemed_by_user_id == uuid.UUID(user_id)
results.append(("redeem same code again -> 409 and no second change", ok and still_ok, str(response.status_code)))

# --- 9. A different user cannot redeem the same code either ------------------------------------
response = client.post("/auth/login", json={"username": USERNAME_2, "password": PASSWORD})
token2 = response.json().get("access_token", "")
response = redeem(CODE_VALID, headers={"Authorization": f"Bearer {token2}"})
with SessionLocal() as db:
    qr = db.execute(select(QRCode).where(QRCode.code == CODE_VALID)).scalar_one()
    still_mine = qr.redeemed_by_user_id == uuid.UUID(user_id)
results.append(("a second user redeeming the same code -> 409, owner unchanged", response.status_code == 409 and still_mine, str(response.status_code)))

# --- 10. Redemption never touches coins (Step 3's job) -------------------------------------------
with SessionLocal() as db:
    user = db.get(User, uuid.UUID(user_id))
    tx_after = db.execute(
        text("SELECT count(*) FROM coin_transactions WHERE user_id = :uid"), {"uid": user_id}
    ).scalar()
ok = user.coin_balance == coin_before and tx_after == tx_before == 0
results.append(
    ("redemption leaves coin_balance and coin_transactions untouched", ok,
     f"coin={user.coin_balance} txs={tx_after}")
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