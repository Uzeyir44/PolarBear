"""
End-to-end checks of POST /clothing/{item_id}/purchase — Phase 3, step 2.

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_clothing_purchase

Covers:
  - Authentication: missing / invalid / expired token -> 401.
  - Successful purchase: 200 with the whitelisted response fields; the
    user_wardrobe row actually created; coin_balance debited by exactly
    the catalog price; exactly ONE coin_transactions row with a negative
    amount, the clothing_purchase type, the correct balance_after, and
    the correct wardrobe_id reference.
  - Insufficient balance -> 400 with NO wardrobe row, NO ledger row, and
    an unchanged balance.
  - Unavailable / upcoming items -> 409 with no database changes.
  - Nonexistent item -> 404 (and a malformed uuid -> 422) with no
    database changes.
  - Duplicate purchase: the second attempt -> 409, the user is charged
    once, one wardrobe row and one purchase ledger row exist.
  - Transaction atomicity: a mid-transaction failure (the clothing_purchase
    lookup row temporarily renamed so the ledger insert cannot resolve its
    type) rolls back the debit AND the wardrobe insert — nothing partial
    survives.
  - Concurrency at the DB layer (two real Postgres connections released
    together on a barrier, driving the same function the endpoint runs):
      * two DIFFERENT items whose combined price equals the whole balance
        -> exactly one succeeds; the balance never goes negative;
      * the SAME item twice -> exactly one 200, one 409, one wardrobe row,
        one ledger row.
  - Concurrency through the HTTP endpoint: simultaneous requests for the
    same item produce exactly one 200 and one 409.

The test creates its OWN category and items (names carry RUN_ID) so it is
isolated from the seeded catalog; all created rows are deleted afterwards.
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
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import (
    AvatarSlot,
    ClothingAvailability,
    ClothingCategory,
    ClothingItem,
    CoinTransaction,
    CoinTransactionType,
    User,
    UserWardrobe,
)
from app.routers.clothing import _purchase_item

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
USERNAME = f"shopper_{RUN_ID}"
PASSWORD = "SuperSecret123!"

PRICE = 100

# Response field whitelist for a successful purchase.
SAFE_RESPONSE_FIELDS = {
    "message",
    "wardrobe_id",
    "item",
    "amount_spent",
    "remaining_balance",
    "transaction_id",
}
# The nested purchased-item object uses the same shape as the browse API.
SAFE_ITEM_FIELDS = {
    "item_id",
    "name",
    "description",
    "category",
    "price",
    "image_url",
    "availability_status",
    "collection_id",
}

TYPE_DISABLED = f"clothing_purchase_disabled_{RUN_ID}"


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


def purchase(item_id, headers=None):
    return client.post(f"/clothing/{item_id}/purchase", headers=headers)


def set_balance(balance: int) -> None:
    with SessionLocal() as db:
        db.execute(
            update(User).where(User.username == USERNAME).values(coin_balance=balance)
        )
        db.commit()


def get_user_id() -> uuid.UUID:
    with SessionLocal() as db:
        return db.execute(
            select(User.user_id).where(User.username == USERNAME)
        ).scalar_one()


def get_balance() -> int:
    with SessionLocal() as db:
        return db.execute(
            select(User.coin_balance).where(User.username == USERNAME)
        ).scalar_one()


def count_wardrobe(item_id) -> int:
    with SessionLocal() as db:
        return db.execute(
            select(text("count(*)"))
            .select_from(UserWardrobe)
            .where(UserWardrobe.item_id == item_id)
        ).scalar()


def count_ledger_for_item(item_id) -> int:
    # Ledger rows for this item join through wardrobe_id.
    with SessionLocal() as db:
        return db.execute(
            select(text("count(*)"))
            .select_from(CoinTransaction)
            .join(UserWardrobe, CoinTransaction.wardrobe_id == UserWardrobe.wardrobe_id)
            .where(UserWardrobe.item_id == item_id)
        ).scalar()


def cleanup(created_category_ids: list, created_item_ids: list) -> None:
    with SessionLocal() as db:
        # Restore the lookup row name in case the atomicity test died midway.
        db.execute(
            update(CoinTransactionType)
            .where(CoinTransactionType.type_name == TYPE_DISABLED)
            .values(type_name="clothing_purchase")
        )
        db.commit()

        user = db.execute(select(User).where(User.username == USERNAME)).scalar_one_or_none()
        if user is not None:
            # coin_transactions.user_id is RESTRICT -> ledger rows go first.
            for tx in db.execute(
                select(CoinTransaction).where(CoinTransaction.user_id == user.user_id)
            ).scalars().all():
                db.delete(tx)
            db.commit()
            # Deleting the user cascades their user_wardrobe rows.
            db.delete(user)
            db.commit()
        for item_id in created_item_ids:
            item = db.get(ClothingItem, item_id)
            if item is not None:
                db.delete(item)
        for category_id in created_category_ids:
            category = db.get(ClothingCategory, category_id)
            if category is not None:
                db.delete(category)
        db.commit()
    print(
        f"\nCleaned up {len(created_item_ids)} items, "
        f"{len(created_category_ids)} categories and 1 test user."
    )


results = []
created_category_ids: list[int] = []
created_item_ids: list[uuid.UUID] = []

try:
    # --- Setup: authenticated user + a private category with test items ----
    response = client.post(
        "/auth/register",
        json={"username": USERNAME, "email": f"{USERNAME}@example.com", "password": PASSWORD},
    )
    results.append(("setup register", response.status_code == 201, str(response.status_code)))

    login = client.post("/auth/login", json={"username": USERNAME, "password": PASSWORD})
    TOKEN = login.json().get("access_token", "")
    AUTH = {"Authorization": f"Bearer {TOKEN}"}
    results.append(("setup login works", bool(TOKEN), ""))

    USER_ID = get_user_id()

    with SessionLocal() as db:
        category = ClothingCategory(category_name=f"PUR_{RUN_ID}", slot=AvatarSlot.ACCESSORY)
        db.add(category)
        db.commit()
        db.refresh(category)
        created_category_ids.append(category.category_id)

        def add_item(name: str, availability: ClothingAvailability) -> uuid.UUID:
            item = ClothingItem(
                name=name,
                description=f"Test item {name}",
                category_id=category.category_id,
                price=PRICE,
                image_url=f"https://example.com/{name}.png",
                availability_status=availability,
            )
            db.add(item)
            db.commit()
            db.refresh(item)
            created_item_ids.append(item.item_id)
            return item.item_id

        ITEM_OK = add_item(f"ok_{RUN_ID}", ClothingAvailability.AVAILABLE)
        ITEM_B = add_item(f"b_{RUN_ID}", ClothingAvailability.AVAILABLE)
        ITEM_C = add_item(f"c_{RUN_ID}", ClothingAvailability.AVAILABLE)
        ITEM_D = add_item(f"d_{RUN_ID}", ClothingAvailability.AVAILABLE)
        # Fresh items reserved exclusively for the concurrency scenarios so
        # leftovers from earlier sections can't skew their row counts.
        ITEM_E = add_item(f"e_{RUN_ID}", ClothingAvailability.AVAILABLE)
        ITEM_F = add_item(f"f_{RUN_ID}", ClothingAvailability.AVAILABLE)
        ITEM_G = add_item(f"g_{RUN_ID}", ClothingAvailability.AVAILABLE)
        ITEM_UNAVAILABLE = add_item(f"unavail_{RUN_ID}", ClothingAvailability.UNAVAILABLE)
        ITEM_UPCOMING = add_item(f"upcoming_{RUN_ID}", ClothingAvailability.UPCOMING)

    def purchase_type_id(db) -> int:
        return db.execute(
            select(CoinTransactionType.type_id).where(
                CoinTransactionType.type_name == "clothing_purchase"
            )
        ).scalar_one()

    # --- 1. Authentication ---------------------------------------------------
    results.append(("purchase without token -> 401", purchase(ITEM_OK).status_code == 401, ""))
    results.append(
        ("purchase with invalid token -> 401",
         purchase(ITEM_OK, {"Authorization": "Bearer not.a.jwt"}).status_code == 401, ""),
    )
    expired = {"Authorization": f"Bearer {make_token(str(USER_ID), timedelta(minutes=-5))}"}
    results.append(("purchase with expired token -> 401", purchase(ITEM_OK, expired).status_code == 401, ""))
    results.append(("purchase with valid token passes auth", purchase(ITEM_UPCOMING, AUTH).status_code == 409, ""))

    # --- 2. Nonexistent / malformed item --------------------------------------
    set_balance(500)
    balance_before = get_balance()
    bogus = uuid.uuid4()
    r = purchase(bogus, AUTH)
    results.append(
        ("nonexistent item -> 404",
         r.status_code == 404 and get_balance() == balance_before
         and count_wardrobe(bogus) == 0 and count_ledger_for_item(bogus) == 0,
         str(r.status_code)),
    )
    results.append(("malformed item id -> 422", purchase("not-a-uuid", AUTH).status_code == 422, ""))

    # --- 3. Successful purchase -------------------------------------------------
    set_balance(500)
    r = purchase(ITEM_OK, AUTH)
    body = r.json()
    ok = (
        r.status_code == 200
        and set(body.keys()) == SAFE_RESPONSE_FIELDS
        and body["message"] == "Item purchased successfully"
        and body["amount_spent"] == PRICE
        and body["remaining_balance"] == 500 - PRICE
    )
    results.append(("valid purchase -> 200 with whitelisted fields", ok, str(body)))

    item_payload = body.get("item", {})
    results.append(
        ("purchased item payload matches catalog shape",
         set(item_payload.keys()) == SAFE_ITEM_FIELDS
         and item_payload.get("item_id") == str(ITEM_OK)
         and item_payload.get("price") == PRICE
         and item_payload.get("availability_status") == "available",
         str(item_payload.get("item_id"))),
    )

    with SessionLocal() as db:
        user = db.get(User, USER_ID)
        wardrobe_row = db.execute(
            select(UserWardrobe).where(
                UserWardrobe.user_id == USER_ID, UserWardrobe.item_id == ITEM_OK
            )
        ).scalar_one_or_none()
        txs = db.execute(
            select(CoinTransaction).where(
                CoinTransaction.wardrobe_id == (wardrobe_row.wardrobe_id if wardrobe_row else uuid.uuid4())
            )
        ).scalars().all()
        expected_type_id = purchase_type_id(db)

    results.append(("wardrobe record created", wardrobe_row is not None, ""))
    results.append(
        ("response wardrobe_id matches DB row",
         wardrobe_row is not None and body["wardrobe_id"] == str(wardrobe_row.wardrobe_id), ""),
    )
    results.append(
        ("coin_balance decreased by exactly the price",
         user.coin_balance == 500 - PRICE, f"balance={user.coin_balance}"),
    )
    results.append(("exactly one ledger row for the purchase", len(txs) == 1, f"{len(txs)}"))

    if len(txs) == 1:
        tx = txs[0]
        results.append(("ledger amount is negative (-price)", tx.amount == -PRICE, str(tx.amount)))
        results.append(("ledger balance_after == new balance", tx.balance_after == 500 - PRICE, str(tx.balance_after)))
        results.append(("ledger user_id == buyer", tx.user_id == USER_ID, str(tx.user_id)))
        results.append(("ledger type_id == clothing_purchase", tx.type_id == expected_type_id, str(tx.type_id)))
        results.append(
            ("ledger wardrobe_id references the new wardrobe row",
             tx.wardrobe_id == wardrobe_row.wardrobe_id
             and body["transaction_id"] == str(tx.transaction_id),
             ""),
        )
        results.append(("ledger created_at set", tx.created_at is not None, ""))
    else:
        for name in (
            "ledger amount is negative (-price)",
            "ledger balance_after == new balance",
            "ledger user_id == buyer",
            "ledger type_id == clothing_purchase",
            "ledger wardrobe_id references the new wardrobe row",
            "ledger created_at set",
        ):
            results.append((name, False, "no single ledger row"))

    # --- 4. Insufficient balance --------------------------------------------------
    set_balance(PRICE - 1)
    r = purchase(ITEM_B, AUTH)
    ok = (
        r.status_code == 400
        and get_balance() == PRICE - 1
        and count_wardrobe(ITEM_B) == 0
        and count_ledger_for_item(ITEM_B) == 0
    )
    results.append(("insufficient balance -> 400 with no changes", ok, str(r.status_code)))

    # Exact balance is enough: price == balance must succeed later (race tests
    # rely on it); here just confirm zero-balance cannot buy.
    set_balance(0)
    r = purchase(ITEM_B, AUTH)
    results.append(
        ("zero balance -> 400, no changes",
         r.status_code == 400 and get_balance() == 0
         and count_wardrobe(ITEM_B) == 0 and count_ledger_for_item(ITEM_B) == 0,
         str(r.status_code)),
    )

    # --- 5. Unavailable / upcoming items ---------------------------------------------
    set_balance(1000)
    balance_before = get_balance()
    for label, target in (("unavailable", ITEM_UNAVAILABLE), ("upcoming", ITEM_UPCOMING)):
        r = purchase(target, AUTH)
        results.append(
            (f"{label} item -> 409 with no changes",
             r.status_code == 409 and get_balance() == balance_before
             and count_wardrobe(target) == 0 and count_ledger_for_item(target) == 0,
             str(r.status_code)),
        )

    # --- 6. Duplicate purchase ---------------------------------------------------------
    set_balance(10 * PRICE)
    first = purchase(ITEM_C, AUTH)
    balance_after_first = get_balance()
    second = purchase(ITEM_C, AUTH)
    ok = (
        first.status_code == 200
        and second.status_code == 409
        and get_balance() == balance_after_first          # charged once
        and count_wardrobe(ITEM_C) == 1                   # one ownership row
        and count_ledger_for_item(ITEM_C) == 1            # one purchase ledger row
    )
    results.append(
        ("duplicate purchase -> 409, charged once, single wardrobe + ledger row",
         ok, f"first={first.status_code} second={second.status_code}"),
    )

    # --- 7. Transaction atomicity: mid-transaction failure rolls everything back ------
    set_balance(5 * PRICE)
    balance_before = get_balance()
    try:
        with SessionLocal() as db:
            db.execute(
                update(CoinTransactionType)
                .where(CoinTransactionType.type_name == "clothing_purchase")
                .values(type_name=TYPE_DISABLED)
            )
            db.commit()

        r = purchase(ITEM_B, AUTH)
        ok = (
            r.status_code == 500
            and get_balance() == balance_before               # debit rolled back
            and count_wardrobe(ITEM_B) == 0                   # ownership rolled back
            and count_ledger_for_item(ITEM_B) == 0            # ledger rolled back
        )
        results.append(
            ("mid-transaction failure rolls back debit + wardrobe + ledger",
             ok, f"status={r.status_code} balance={get_balance()}"),
        )
    finally:
        with SessionLocal() as db:
            db.execute(
                update(CoinTransactionType)
                .where(CoinTransactionType.type_name == TYPE_DISABLED)
                .values(type_name="clothing_purchase")
            )
            db.commit()

    with SessionLocal() as db:
        restored = db.execute(
            select(CoinTransactionType.type_id).where(
                CoinTransactionType.type_name == "clothing_purchase"
            )
        ).scalar_one_or_none()
    results.append(("clothing_purchase lookup row restored afterwards", restored is not None, ""))

    # --- 8. DB-layer concurrency: two items, combined price == whole balance ----------
    # Both workers run the SAME function the endpoint runs, each on its own real
    # connection, synchronized on a barrier so they genuinely contend.
    set_balance(PRICE)  # exactly enough for ONE of the two items
    barrier = threading.Barrier(2)
    db_outcomes = []

    def race_two_items() -> None:
        barrier.wait()
        with SessionLocal() as db:
            user = db.get(User, USER_ID)
            target = ITEM_E if threading.current_thread().name.endswith("_1") else ITEM_F
            try:
                _purchase_item(db, target, user)
                db_outcomes.append(200)
            except Exception:
                db_outcomes.append(400)

    threads = [threading.Thread(target=race_two_items, name=f"race_two_items_{i}") for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    bought_ok = count_wardrobe(ITEM_E) + count_wardrobe(ITEM_F)
    ledger_rows = count_ledger_for_item(ITEM_E) + count_ledger_for_item(ITEM_F)
    final_balance = get_balance()
    ok = (
        sorted(db_outcomes) == [200, 400]          # exactly one winner
        and bought_ok == 1                          # exactly one ownership row
        and ledger_rows == 1                        # exactly one ledger row
        and final_balance == 0                      # spent once, never negative
    )
    results.append(
        ("concurrent buys of two 100-coin items with 100 coins -> one 200, one 400, balance 0",
         ok, f"outcomes={db_outcomes} balance={final_balance} wardrobes={bought_ok} ledgers={ledger_rows}"),
    )

    # --- 9. DB-layer concurrency: same item twice ---------------------------------------
    set_balance(5 * PRICE)
    barrier = threading.Barrier(2)
    db_outcomes = []

    def race_same_item() -> None:
        barrier.wait()
        with SessionLocal() as db:
            user = db.get(User, USER_ID)
            try:
                _purchase_item(db, ITEM_B, user)
                db_outcomes.append(200)
            except Exception:
                db_outcomes.append(409)

    threads = [threading.Thread(target=race_same_item, name=f"race_same_{i}") for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = (
        sorted(db_outcomes) == [200, 409]
        and count_wardrobe(ITEM_B) == 1
        and count_ledger_for_item(ITEM_B) == 1
        and get_balance() == 5 * PRICE - PRICE      # charged exactly once
    )
    results.append(
        ("concurrent buys of the SAME item -> one 200, one 409, charged once",
         ok, f"outcomes={db_outcomes} balance={get_balance()}"),
    )

    # --- 10. Concurrency through the HTTP endpoint ----------------------------------------
    set_balance(5 * PRICE)
    barrier = threading.Barrier(2)
    http_outcomes = []

    def http_race() -> None:
        barrier.wait()
        http_outcomes.append(purchase(ITEM_G, AUTH).status_code)

    threads = [threading.Thread(target=http_race, name=f"http_race_{i}") for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = (
        sorted(http_outcomes) == [200, 409]
        and count_wardrobe(ITEM_G) == 1
        and count_ledger_for_item(ITEM_G) == 1
        and get_balance() == 5 * PRICE - PRICE
    )
    results.append(
        ("concurrent HTTP requests for the same item -> one 200, one 409, charged once",
         ok, f"outcomes={http_outcomes} balance={get_balance()}"),
    )

finally:
    cleanup(created_category_ids, created_item_ids)

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)
