"""
End-to-end check of the coin reading endpoints:
GET /users/me/coins and GET /users/me/transactions.

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_user_coins

Covers: missing/invalid/expired token -> 401; balance matches PostgreSQL;
a real QR redemption shows up in history with its QR/product reference;
newest transactions appear first; limit/offset pagination works with no
overlap; only the authenticated user's transactions ever appear (three
users with separate histories, plus a user_id query param that must be
ignored); empty history -> []; invalid limit/offset -> 422; and the
response exposes only the whitelisted fields.

History rows are inserted directly into coin_transactions with explicit
created_at values (relative to now) so ordering and pagination checks are
fully deterministic. One transaction is created by actually redeeming a QR
code, proving the read endpoints surface the redemption that Step 3 writes.

All test products, qr_codes, users, and their coin_transactions are
deleted afterwards so the dev DB is left clean.
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
from app.models import (
    QRCode,
    QRStatus,
    CoinTransaction,
    CoinTransactionType,
    Product,
    User,
)

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

USER_A = f"coins_a_{RUN_ID}"
USER_B = f"coins_b_{RUN_ID}"
USER_EMPTY = f"coins_empty_{RUN_ID}"

# The code that gets REDEEMED through the real endpoint (integration).
CODE_REDEEM = f"COLA-{RUN_ID}-REDEEM"
REDEEM_VALUE = 30
# The code a directly-inserted ledger row references (also real, exists).
CODE_REF = f"COLA-{RUN_ID}-REF"
PRODUCT_NAME = "Cola 330ml Zero"

TX_FIELDS = {
    "transaction_id",
    "amount",
    "balance_after",
    "transaction_type",
    "direction",
    "created_at",
    "qr",
    "competition_id",
    "wardrobe_id",
    "vote_id",
}
QR_FIELDS = {"qr_id", "code", "product_name"}
LEAKED = {"user_id", "type_id", "qr_code", "wardrobe_entry", "type"}

created_codes = [CODE_REDEEM, CODE_REF]


def report(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({extra})" if extra else ""))


def make_token(user_id: str, expires_delta: timedelta) -> str:
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + expires_delta,
    }
    return pyjwt.encode(payload, settings.secret_key, algorithm="HS256")


def type_id_of(db, type_name: str) -> int:
    return db.execute(
        select(CoinTransactionType.type_id).where(
            CoinTransactionType.type_name == type_name
        )
    ).scalar_one()


def cleanup() -> None:
    with SessionLocal() as db:
        # Delete ledger rows first (RESTRICT FKs on user_id and qr_id), then
        # the qr_codes they reference, then the users, then the product.
        for username in (USER_A, USER_B, USER_EMPTY):
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is not None:
                for tx in db.execute(
                    select(CoinTransaction).where(CoinTransaction.user_id == user.user_id)
                ).scalars().all():
                    db.delete(tx)
        db.commit()
        for code in created_codes:
            qr = db.execute(select(QRCode).where(QRCode.code == code)).scalar_one_or_none()
            if qr is not None:
                db.delete(qr)
        db.commit()
        for username in (USER_A, USER_B, USER_EMPTY):
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
        f"\nCleaned up {len(created_codes)} qr code(s), the product, and 3 test users."
    )


results = []

# --- Setup: register three users and log in as A and B ----------------------
for username in (USER_A, USER_B, USER_EMPTY):
    response = client.post(
        "/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
    )
    results.append(
        (f"setup register {username}", response.status_code == 201, str(response.status_code))
    )

response = client.post("/auth/login", json={"username": USER_A, "password": PASSWORD})
token_a = response.json().get("access_token", "")
AUTH_A = {"Authorization": f"Bearer {token_a}"}
results.append(("setup login A", bool(token_a), ""))

response = client.post("/auth/login", json={"username": USER_B, "password": PASSWORD})
token_b = response.json().get("access_token", "")
AUTH_B = {"Authorization": f"Bearer {token_b}"}
results.append(("setup login B", bool(token_b), ""))

with SessionLocal() as db:
    USER_A_ID = str(db.execute(select(User.user_id).where(User.username == USER_A)).scalar_one())
    USER_B_ID = str(db.execute(select(User.user_id).where(User.username == USER_B)).scalar_one())
    USER_EMPTY_ID = str(db.execute(select(User.user_id).where(User.username == USER_EMPTY)).scalar_one())

    product = Product(name=PRODUCT_NAME, sku=f"SKU-{RUN_ID}")
    db.add(product)
    db.flush()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    db.add_all(
        [
            QRCode(code=CODE_REDEEM, product_id=product.product_id,
                   coin_value=REDEEM_VALUE, status=QRStatus.ACTIVE, expires_at=future),
            QRCode(code=CODE_REF, product_id=product.product_id,
                   coin_value=5, status=QRStatus.ACTIVE, expires_at=future),
        ]
    )
    db.commit()
    QR_REF_ID = str(db.execute(select(QRCode.qr_id).where(QRCode.code == CODE_REF)).scalar_one())


# --- 1. One REAL redemption (integration with Step 3) -----------------------
response = client.post("/qr/redeem", json={"code": CODE_REDEEM}, headers=AUTH_A)
results.append(("real redemption of CODE_REDEEM -> 200", response.status_code == 200, str(response.status_code)))

# --- 2. Directly-seeded, deterministic history ------------------------------
# (created_at = now - N days, type_name, amount, running balance_after, qr ref)
A_SEED = [
    (100, "qr_redemption", 10, 10, None),
    (80, "competition_reward", 15, 25, None),
    (60, "vote_cast", -5, 20, None),
    (40, "refund", 50, 70, None),
    (20, "qr_redemption", 30, 100, QR_REF_ID),
]
B_SEED = [
    (10, "qr_redemption", 9, 9, None),
    (5, "competition_reward", 90, 99, None),
]

with SessionLocal() as db:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    a_id = uuid.UUID(USER_A_ID)
    for n, type_name, amount, bal_after, qr_id in A_SEED:
        db.add(
            CoinTransaction(
                user_id=a_id,
                type_id=type_id_of(db, type_name),
                amount=amount,
                balance_after=bal_after,
                qr_id=uuid.UUID(qr_id) if qr_id else None,
                created_at=now - timedelta(days=n),
            )
        )
    b_id = uuid.UUID(USER_B_ID)
    for n, type_name, amount, bal_after, qr_id in B_SEED:
        db.add(
            CoinTransaction(
                user_id=b_id,
                type_id=type_id_of(db, type_name),
                amount=amount,
                balance_after=bal_after,
                qr_id=uuid.UUID(qr_id) if qr_id else None,
                created_at=now - timedelta(days=n),
            )
        )
    # A's balance cache = 100 from the seeds + 30 from the real redemption.
    db.get(User, a_id).coin_balance = 100 + REDEEM_VALUE
    db.get(User, b_id).coin_balance = 99
    db.commit()

# --- 3. Auth: missing / invalid / expired token -> 401 -----------------------
missing_ok = client.get("/users/me/coins").status_code == 401
invalid_ok = client.get(
    "/users/me/coins", headers={"Authorization": "Bearer not.a.jwt"}
).status_code == 401
expired_ok = client.get(
    "/users/me/coins",
    headers={"Authorization": f"Bearer {make_token(USER_A_ID, timedelta(minutes=-5))}"},
).status_code == 401
missing_t = client.get("/users/me/transactions").status_code == 401
invalid_t = client.get(
    "/users/me/transactions", headers={"Authorization": "Bearer not.a.jwt"}
).status_code == 401
expired_t = client.get(
    "/users/me/transactions",
    headers={"Authorization": f"Bearer {make_token(USER_A_ID, timedelta(minutes=-5))}"},
).status_code == 401
results.append(
    ("missing/invalid/expired token -> 401 on both endpoints",
     missing_ok and invalid_ok and expired_ok and missing_t and invalid_t and expired_t,
     "")
)

# --- 4. Balance matches PostgreSQL ------------------------------------------
response = client.get("/users/me/coins", headers=AUTH_A)
with SessionLocal() as db:
    db_balance = db.get(User, uuid.UUID(USER_A_ID)).coin_balance
ok = (
    response.status_code == 200
    and response.json() == {"balance": db_balance}
    and db_balance == 130
)
results.append(
    ("GET /users/me/coins returns the DB coin_balance", ok, str(response.json()))
)

# A user_id query param must be ignored: still A's own balance.
response = client.get(f"/users/me/coins?user_id={USER_B_ID}", headers=AUTH_A)
ok = response.status_code == 200 and response.json() == {"balance": db_balance}
results.append(("client-supplied user_id is ignored", ok, str(response.json())))

# --- 5. Full history: newest first, correct shape ---------------------------
response = client.get("/users/me/transactions", headers=AUTH_A)
body = response.json()
expected_amount_order = [REDEEM_VALUE, 30, 50, -5, 15, 10]  # RE, r5, r4, r3, r2, r1
amounts = [tx["amount"] for tx in body]
results.append(("history contains all 6 transactions", len(body) == 6, f"{len(body)}"))
results.append(("transactions ordered newest -> oldest", amounts == expected_amount_order, str(amounts)))

all_fields_ok = all(set(tx) == TX_FIELDS and not (LEAKED & set(tx)) for tx in body)
results.append(
    ("every row exposes exactly the whitelisted fields",
     all_fields_ok, str(sorted(body[0].keys())))
)

newest = body[0]
qr_ref = newest.get("qr")
ok_qr = (
    newest["transaction_type"] == "qr_redemption"
    and newest["direction"] == "CREDIT"
    and newest["amount"] == REDEEM_VALUE
    and qr_ref is not None
    and qr_ref["code"] == CODE_REDEEM
    and qr_ref["product_name"] == PRODUCT_NAME
)
results.append(
    ("newest (real redemption) carries QR/product reference", ok_qr, str(qr_ref))
)
results.append(
    ("qr sub-object exposes only qr_id/code/product_name",
     set(qr_ref) == QR_FIELDS,
     str(sorted(qr_ref.keys())))
)

# A debit type renders direction DEBIT and a negative amount.
refund_row = next(tx for tx in body if tx["transaction_type"] == "vote_cast")
ok_debit = refund_row["direction"] == "DEBIT" and refund_row["amount"] == -5
results.append(("debit transaction -> direction DEBIT, negative amount", ok_debit, str(refund_row)))

# The directly-seeded qr_redemption row references CODE_REF.
ref_row = next(tx for tx in body if tx.get("qr") and tx["qr"]["code"] == CODE_REF)
ok_ref = ref_row["qr"]["qr_id"] == QR_REF_ID and ref_row["qr"]["product_name"] == PRODUCT_NAME
results.append(
    ("directly-seeded qr row references the right code/product", ok_ref, str(ref_row["qr"]))
)

# --- 6. Pagination ----------------------------------------------------------
ids_all = [tx["transaction_id"] for tx in body]


def page(limit: int, offset: int):
    r = client.get(
        f"/users/me/transactions?limit={limit}&offset={offset}", headers=AUTH_A
    )
    return r.status_code, [tx["transaction_id"] for tx in r.json()]


s, p0 = page(2, 0)
s2, p2 = page(2, 2)
s4, p4 = page(2, 4)
s6, p6 = page(2, 6)
ok_pages = (
    s == s2 == s4 == s6 == 200
    and p0 + p2 + p4 == ids_all                     # pages tile the whole history
    and len(p0) == len(p2) == len(p4) == 2
    and p6 == []                                    # beyond the end -> empty
)
results.append(
    ("pagination tiles the full history with no overlap", ok_pages,
     f"p0={len(p0)} p2={len(p2)} p4={len(p4)} p6={len(p6)}")
)

r = client.get("/users/me/transactions?limit=50&offset=0", headers=AUTH_A)
ok_limit50 = r.status_code == 200 and len(r.json()) == 6
results.append(("limit=50 returns everything", ok_limit50, f"{len(r.json())}"))

# --- 7. Isolation: another user's history is invisible ----------------------
r_b = client.get("/users/me/transactions", headers=AUTH_B)
body_b = r_b.json()
ids_b = [tx["transaction_id"] for tx in body_b]
ok_isol = (
    len(body_b) == 2
    and len(set(ids_all) & set(ids_b)) == 0      # no shared ids
)
results.append(("user B sees only B's transactions", ok_isol, f"B={len(body_b)} A={len(ids_all)}"))

r_b_bal = client.get("/users/me/coins", headers=AUTH_B)
ok_bal_b = r_b_bal.json() == {"balance": 99}
results.append(("user B's balance is separate", ok_bal_b, str(r_b_bal.json())))

# --- 8. Empty history -> [] -------------------------------------------------
r = client.get("/users/me/transactions", headers={"Authorization": f"Bearer {make_token(USER_EMPTY_ID, timedelta(minutes=5))}"})
ok_empty = r.status_code == 200 and r.json() == []
results.append(("empty history -> 200 with []", ok_empty, str(r.json())))

# --- 9. Validation: bad limit/offset -> 422 --------------------------------
bad_params = [
    ("limit=0", f"/users/me/transactions?limit=0"),
    ("limit=51", f"/users/me/transactions?limit=51"),
    ("limit=-1", f"/users/me/transactions?limit=-1"),
    ("limit=abc", f"/users/me/transactions?limit=abc"),
    ("offset=-1", f"/users/me/transactions?offset=-1"),
]
validation_all = all(
    client.get(path, headers=AUTH_A).status_code == 422 for name, path in bad_params
)
results.append(("invalid limit/offset -> 422", validation_all, str(len(bad_params))))

r = client.get("/users/me/transactions?limit=1", headers=AUTH_A)
ok_limit1 = r.status_code == 200 and len(r.json()) == 1
results.append(("valid boundary limit=1 -> 200 with 1 row", ok_limit1, f"{len(r.json())}"))

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