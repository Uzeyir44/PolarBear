"""
Deep end-to-end checks of POST /qr/redeem — Step 3: award coins.

Run from the backend/ directory:
    venv/Scripts/python -m app.test_qr_redeem_coins

test_qr_redeem.py skims the coin side; this file verifies it in depth:

  - a valid redemption credits exactly `coin_value` coins,
  - it writes exactly ONE coin_transactions ledger row with the right
    amount, balance_after, user_id, type_id (qr_redemption) and qr_id,
  - the QR row actually becomes REDEEMED by that user,
  - redeeming the same code twice never awards coins twice,
  - a redemption that fails mid-transaction (after some writes) rolls ALL
    of it back — the code is still ACTIVE, the balance is unchanged, and
    no ledger row exists,
  - two concurrent redemption attempts for the same code — both at the DB
    transaction layer (two real Postgres connections synchronized on a
    barrier) and through the HTTP endpoint — cannot both succeed: exactly
    one awards the coins.

The DB-layer concurrency attempt uses the same function the endpoint runs
(_redeem_qr) directly, so the two attempts genuinely block on the row lock
instead of being serialized by the test client.

Test products, qr_codes, users, and their coin_transactions are deleted
afterwards so the dev DB is left clean.
"""
import threading
import time
import uuid
import warnings
from datetime import datetime, timedelta, timezone

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

import jwt as pyjwt
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import QRCode, QRStatus, CoinTransaction, CoinTransactionType, Product, User
from app.routers.qr import _redeem_qr

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
USERNAME = f"coinuser_{RUN_ID}"
EMAIL = f"{USERNAME}@example.com"
PASSWORD = "SuperSecret123!"

CODE_A = f"COLA-{RUN_ID}-A"
CODE_B = f"COLA-{RUN_ID}-B"
CODE_DOUBLE = f"COLA-{RUN_ID}-DOUBLE"
CODE_FAIL = f"COLA-{RUN_ID}-FAIL"
CODE_RACE = f"COLA-{RUN_ID}-RACE"
CODE_HTTP_RACE = f"COLA-{RUN_ID}-HTTPRACE"

COIN_A = 15
COIN_B = 7
COIN_DOUBLE = 12
COIN_FAIL = 9
COIN_RACE = 20
COIN_HTTP_RACE = 5

created_codes = [CODE_A, CODE_B, CODE_DOUBLE, CODE_FAIL, CODE_RACE, CODE_HTTP_RACE]

TYPE_DISABLED = f"qr_redemption_disabled_{RUN_ID}"


def report(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({extra})" if extra else ""))


def redeem(code: str, headers: dict | None = None):
    return client.post("/qr/redeem", json={"code": code}, headers=headers)


def make_token(user_id: str, expires_delta: timedelta) -> str:
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + expires_delta,
    }
    return pyjwt.encode(payload, settings.secret_key, algorithm="HS256")


def qr_redemption_type_id(db) -> int:
    return db.execute(
        select(CoinTransactionType.type_id).where(
            CoinTransactionType.type_name == "qr_redemption"
        )
    ).scalar_one()


def cleanup() -> None:
    with SessionLocal() as db:
        # coin_transactions FKs (user_id, qr_id) are RESTRICT, so ledger rows
        # must go before the qr_codes and user they reference.
        user = db.execute(select(User).where(User.username == USERNAME)).scalar_one_or_none()
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
        if user is not None:
            db.delete(user)
        db.commit()
        product = db.execute(
            select(Product).where(Product.sku == f"SKU-{RUN_ID}")
        ).scalar_one_or_none()
        if product is not None:
            db.delete(product)
        db.commit()
    print(f"\nCleaned up {len(created_codes)} qr code(s), the product, and 1 test user.")


results = []

# --- Setup: register a user, seed one product with six active codes -----------
response = client.post(
    "/auth/register",
    json={"username": USERNAME, "email": EMAIL, "password": PASSWORD},
)
results.append(("setup register", response.status_code == 201, str(response.status_code)))

with SessionLocal() as db:
    USER_ID = str(
        db.execute(select(User.user_id).where(User.username == USERNAME)).scalar_one()
    )
    product = Product(name="Cola 330ml", sku=f"SKU-{RUN_ID}")
    db.add(product)
    db.flush()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    db.add_all(
        [
            QRCode(code=CODE_A, product_id=product.product_id, coin_value=COIN_A,
                   status=QRStatus.ACTIVE, expires_at=future),
            QRCode(code=CODE_B, product_id=product.product_id, coin_value=COIN_B,
                   status=QRStatus.ACTIVE, expires_at=future),
            QRCode(code=CODE_DOUBLE, product_id=product.product_id, coin_value=COIN_DOUBLE,
                   status=QRStatus.ACTIVE, expires_at=future),
            QRCode(code=CODE_FAIL, product_id=product.product_id, coin_value=COIN_FAIL,
                   status=QRStatus.ACTIVE, expires_at=future),
            QRCode(code=CODE_RACE, product_id=product.product_id, coin_value=COIN_RACE,
                   status=QRStatus.ACTIVE, expires_at=future),
            QRCode(code=CODE_HTTP_RACE, product_id=product.product_id, coin_value=COIN_HTTP_RACE,
                   status=QRStatus.ACTIVE, expires_at=future),
        ]
    )
    db.commit()
    qr_ids = {
        code: str(
            db.execute(select(QRCode.qr_id).where(QRCode.code == code)).scalar_one()
        )
        for code in created_codes
    }

response = client.post("/auth/login", json={"username": USERNAME, "password": PASSWORD})
token = response.json().get("access_token", "")
AUTH = {"Authorization": f"Bearer {token}"}
results.append(("setup login works", bool(token), ""))

with SessionLocal() as db:
    balance0 = db.get(User, uuid.UUID(USER_ID)).coin_balance


# --- 1. Valid redemption: correct coins, balance, ledger, QR state ------------
response = redeem(CODE_A, headers=AUTH)
body = response.json()
ok = (
    response.status_code == 200
    and body.get("message") == "Code redeemed successfully"
    and body.get("coins_earned") == COIN_A
    and body.get("balance") == balance0 + COIN_A
)
results.append(("valid redemption -> 200 with coins_earned and new balance", ok, str(body)))

with SessionLocal() as db:
    user = db.get(User, uuid.UUID(USER_ID))
    qr = db.execute(select(QRCode).where(QRCode.code == CODE_A)).scalar_one()
    txs = db.execute(
        select(CoinTransaction).where(CoinTransaction.user_id == uuid.UUID(USER_ID))
    ).scalars().all()
    tx_type_id = qr_redemption_type_id(db)

results.append(
    ("coin_balance increased by exactly coin_value",
     user.coin_balance == balance0 + COIN_A, f"balance={user.coin_balance}")
)
results.append(
    ("QR becomes REDEEMED with redeemer + timestamp",
     qr.status == QRStatus.REDEEMED
     and qr.redeemed_by_user_id == uuid.UUID(USER_ID)
     and qr.redeemed_at is not None,
     f"{qr.status}")
)
results.append(("exactly one CoinTransaction created", len(txs) == 1, f"{len(txs)}"))

tx = txs[0]
results.append(("ledger amount == coin_value", tx.amount == COIN_A, str(tx.amount)))
results.append(("ledger balance_after == new balance", tx.balance_after == balance0 + COIN_A, str(tx.balance_after)))
results.append(("ledger user_id == redeemer", tx.user_id == uuid.UUID(USER_ID), str(tx.user_id)))
results.append(("ledger type_id == qr_redemption", tx.type_id == tx_type_id, str(tx.type_id)))
results.append(("ledger qr_id == redeemed code's qr_id", tx.qr_id == uuid.UUID(qr_ids[CODE_A]), str(tx.qr_id)))


# --- 2. Second redemption stacks: balance_after is the running total ----------
expected = balance0 + COIN_A + COIN_B
response = redeem(CODE_B, headers=AUTH)
body = response.json()
with SessionLocal() as db:
    user = db.get(User, uuid.UUID(USER_ID))
    tx_b = db.execute(
        select(CoinTransaction).where(CoinTransaction.qr_id == uuid.UUID(qr_ids[CODE_B]))
    ).scalar_one()
ok = (
    response.status_code == 200
    and body.get("coins_earned") == COIN_B
    and body.get("balance") == expected
    and user.coin_balance == expected
    and tx_b.amount == COIN_B
    and tx_b.balance_after == expected
)
results.append(
    ("second redemption stacks on the first (balance_after == running total)",
     ok, f"balance={user.coin_balance}")
)


# --- 3. Redeeming the same code twice never awards coins twice ----------------
response = redeem(CODE_DOUBLE, headers=AUTH)
first_ok = response.status_code == 200
with SessionLocal() as db:
    bal_after_first = db.get(User, uuid.UUID(USER_ID)).coin_balance
    owner = db.execute(
        select(QRCode).where(QRCode.code == CODE_DOUBLE)
    ).scalar_one().redeemed_by_user_id

response = redeem(CODE_DOUBLE, headers=AUTH)
second_status = response.status_code
with SessionLocal() as db:
    bal_after_second = db.get(User, uuid.UUID(USER_ID)).coin_balance
    double_tx = db.execute(
        text("SELECT count(*) FROM coin_transactions WHERE qr_id = :q"),
        {"q": qr_ids[CODE_DOUBLE]},
    ).scalar()

ok = (
    first_ok
    and second_status == 409
    and bal_after_second == bal_after_first       # no second credit
    and owner == uuid.UUID(USER_ID)               # original owner preserved
    and double_tx == 1                            # still exactly one ledger row
)
results.append(
    ("redeeming the same code again -> 409 with no second credit",
     ok, f"status={second_status} balance={bal_after_second} txs={double_tx}")
)


# --- 4. A failed transaction rolls back claim, credit, and ledger ------------
with SessionLocal() as db:
    bal_before_fail = db.get(User, uuid.UUID(USER_ID)).coin_balance

# Rename the qr_redemption lookup row so the endpoint's type lookup comes back
# empty AFTER it has already staged the QR claim and the balance credit. The
# endpoint must detect that, roll everything back, and return 500. The name is
# restored in the finally block so a failure here can't poison later runs.
try:
    with SessionLocal() as db:
        db.execute(
            update(CoinTransactionType)
            .where(CoinTransactionType.type_name == "qr_redemption")
            .values(type_name=TYPE_DISABLED)
        )
        db.commit()

    response = redeem(CODE_FAIL, headers=AUTH)
    with SessionLocal() as db:
        qr = db.execute(select(QRCode).where(QRCode.code == CODE_FAIL)).scalar_one()
        user = db.get(User, uuid.UUID(USER_ID))
        fail_tx = db.execute(
            text("SELECT count(*) FROM coin_transactions WHERE qr_id = :q"),
            {"q": qr_ids[CODE_FAIL]},
        ).scalar()

    ok = (
        response.status_code == 500
        and qr.status == QRStatus.ACTIVE               # claim rolled back
        and qr.redeemed_by_user_id is None
        and qr.redeemed_at is None
        and user.coin_balance == bal_before_fail       # credit rolled back
        and fail_tx == 0                               # ledger rolled back
    )
    results.append(
        ("mid-transaction failure rolls back claim + credit + ledger",
         ok,
         f"status={response.status_code} qr={qr.status} balance={user.coin_balance} txs={fail_tx}")
    )
finally:
    with SessionLocal() as db:
        db.execute(
            update(CoinTransactionType)
            .where(CoinTransactionType.type_name == TYPE_DISABLED)
            .values(type_name="qr_redemption")
        )
        db.commit()

with SessionLocal() as db:
    restored = (
        db.execute(
            select(CoinTransactionType.type_id).where(
                CoinTransactionType.type_name == "qr_redemption"
            )
        ).scalar_one_or_none()
        is not None
    )
results.append(("qr_redemption lookup row restored afterwards", restored, ""))


# --- 5. Concurrency at the DB layer: two real connections, one winner ---------
barrier = threading.Barrier(2)
db_outcomes = []


def db_worker() -> None:
    barrier.wait()
    with SessionLocal() as db:
        user = db.get(User, uuid.UUID(USER_ID))
        try:
            _redeem_qr(db, CODE_RACE, user)
            db_outcomes.append(200)
        except HTTPException as exc:
            db_outcomes.append(exc.status_code)


t1 = threading.Thread(target=db_worker)
t2 = threading.Thread(target=db_worker)
t1.start(); t2.start()
t1.join(); t2.join()

with SessionLocal() as db:
    user = db.get(User, uuid.UUID(USER_ID))
    race_tx = db.execute(
        text("SELECT count(*) FROM coin_transactions WHERE qr_id = :q"),
        {"q": qr_ids[CODE_RACE]},
    ).scalar()
    expected_balance = balance0 + (
        COIN_A + COIN_B + COIN_DOUBLE + COIN_RACE  # CODE_FAIL awarded nothing
    )

ok = (
    sorted(db_outcomes) == [200, 409]                       # exactly one winner
    and user.coin_balance == expected_balance               # coins awarded once
    and race_tx == 1                                        # one ledger row
)
results.append(
    ("concurrent DB attempts on same code -> exactly one 200, one 409, one credit",
     ok,
     f"outcomes={db_outcomes} balance={user.coin_balance} txs={race_tx}")
)


# --- 6. Concurrency through the HTTP endpoint: same guarantee -----------------
barrier = threading.Barrier(2)
http_outcomes = []


def http_worker() -> None:
    barrier.wait()
    r = client.post("/qr/redeem", json={"code": CODE_HTTP_RACE}, headers=AUTH)
    http_outcomes.append(r.status_code)


t1 = threading.Thread(target=http_worker)
t2 = threading.Thread(target=http_worker)
t1.start(); t2.start()
t1.join(); t2.join()

with SessionLocal() as db:
    user = db.get(User, uuid.UUID(USER_ID))
    http_tx = db.execute(
        text("SELECT count(*) FROM coin_transactions WHERE qr_id = :q"),
        {"q": qr_ids[CODE_HTTP_RACE]},
    ).scalar()
    final_balance = user.coin_balance

ok = (
    sorted(http_outcomes) == [200, 409]
    and final_balance == balance0 + (
        COIN_A + COIN_B + COIN_DOUBLE + COIN_RACE + COIN_HTTP_RACE
    )
    and http_tx == 1
)
results.append(
    ("concurrent HTTP requests on same code -> exactly one 200, one 409, one credit",
     ok,
     f"outcomes={http_outcomes} balance={final_balance} txs={http_tx}")
)

# --- 7. Auth regression: missing/invalid/expired token still 401 ---------------
missing_ok = redeem(CODE_A).status_code == 401
invalid_ok = redeem(CODE_A, headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401
expired_token = make_token(USER_ID, timedelta(minutes=-5))
expired_ok = redeem(
    CODE_A, headers={"Authorization": f"Bearer {expired_token}"}
).status_code == 401
results.append(
    ("missing/invalid/expired token -> 401 still intact",
     missing_ok and invalid_ok and expired_ok,
     f"{missing_ok}/{invalid_ok}/{expired_ok}")
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