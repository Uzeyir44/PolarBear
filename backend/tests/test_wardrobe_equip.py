"""
End-to-end checks of the wardrobe equip/unequip endpoints — Phase 4,
part 2:

    POST   /wardrobe/{wardrobe_id}/equip
    DELETE /wardrobe/{wardrobe_id}/equip

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_wardrobe_equip

Covers:
  - Authentication: missing / invalid / expired token -> 401 on BOTH
    endpoints; an authenticated user gets through.
  - Missing avatar: only reachable for a LEGACY user whose avatar row
    is absent (pre-backfill data, simulated here by deleting the row);
    such a user gets an explicit 404 "Avatar not found" (no silent
    avatar creation inside a clothing endpoint). Registration itself
    creates the avatar, so newly registered users can equip right away.
  - Successful equip: one avatar_equipment row exists with the caller's
    avatar, the slot derived from clothing_categories, the owned item,
    and a server-written equipped_at.
  - Ownership isolation (mandatory): user A equipping/unequipping user
    B's wardrobe_id -> 404, and NEITHER user's equipment changes. The
    ownership filter lives in the SQL WHERE clause, not in app code.
  - Slot determination: items from all six categories (hair/hat/top/
    bottom/shoes/accessory) always land in their category's slot —
    there is no client slot input at all.
  - Replacement: equipping Shirt B over Shirt A in TOP leaves exactly
    ONE equipment row (Shirt B), Shirt A stays owned in user_wardrobe,
    nothing is charged.
  - Multiple slots: TOP + BOTTOM + SHOES coexist (the (avatar_id, slot)
    PK only restricts one item PER slot).
  - Unequip: the equipment row is removed; the wardrobe record remains.
  - Incorrect unequip: with Shirt A equipped in TOP, unequipping
    Shirt B -> 409 and Shirt A stays equipped.
  - Availability independence: an equipped item marked UNAVAILABLE by
    an admin stays equipped and can still be unequipped.
  - Coin safety: equip/unequip never touch coin_balance and never write
    coin_transactions rows.
  - Concurrency: two simultaneous HTTP equips of different items into
    the same slot end with exactly ONE equipment row holding one of the
    two items (the PK/upsert is the authority).
  - Data safety: response payloads expose only whitelisted fields.

The test creates its OWN users/avatars/categories/items (names carry
RUN_ID) so it is isolated from seeded data; everything is deleted
afterwards.
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
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import (
    Avatar,
    AvatarEquipment,
    AvatarSlot,
    ClothingAvailability,
    ClothingCategory,
    ClothingItem,
    CoinTransaction,
    User,
    UserWardrobe,
)

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

SAFE_EQUIP_FIELDS = {"message", "equipment"}
SAFE_EQUIPMENT_FIELDS = {"avatar_id", "slot", "equipped_at", "item"}
SAFE_UNEQUIP_FIELDS = {"message", "avatar_id", "slot"}
SAFE_ITEM_FIELDS = {
    "item_id", "name", "description", "category",
    "price", "image_url", "availability_status", "collection_id",
}
SAFE_CATEGORY_FIELDS = {"category_id", "category_name", "slot"}

ALL_SLOTS = [
    AvatarSlot.HAIR,
    AvatarSlot.HAT,
    AvatarSlot.TOP,
    AvatarSlot.BOTTOM,
    AvatarSlot.SHOES,
    AvatarSlot.ACCESSORY,
]


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


def register_and_login(username: str) -> tuple[uuid.UUID, dict]:
    """Register + login. Registration itself creates the user's avatar."""
    client.post(
        "/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
    )
    login = client.post("/auth/login", json={"username": username, "password": PASSWORD})
    headers = {"Authorization": f"Bearer {login.json().get('access_token', '')}"}
    with SessionLocal() as db:
        user_id = db.execute(select(User.user_id).where(User.username == username)).scalar_one()
    return user_id, headers


def delete_avatar(user_id: uuid.UUID) -> None:
    """Simulate a LEGACY user (registered before the avatar lifecycle
    existed / before the backfill) by removing their avatar row."""
    with SessionLocal() as db:
        avatar = db.execute(select(Avatar).where(Avatar.user_id == user_id)).scalar_one_or_none()
        if avatar is not None:
            db.delete(avatar)
            db.commit()


def add_category(slot: AvatarSlot) -> int:
    with SessionLocal() as db:
        category = ClothingCategory(category_name=f"{slot.value}_{RUN_ID}", slot=slot)
        db.add(category)
        db.commit()
        db.refresh(category)
        created_category_ids.append(category.category_id)
        return category.category_id


def add_item(category_id: int, name: str, availability: ClothingAvailability = ClothingAvailability.AVAILABLE) -> uuid.UUID:
    with SessionLocal() as db:
        item = ClothingItem(
            name=name,
            description=f"Test item {name}",
            category_id=category_id,
            price=100,
            image_url=f"https://example.com/{name}.png",
            availability_status=availability,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        created_item_ids.append(item.item_id)
        return item.item_id


def grant_ownership(user_id: uuid.UUID, item_id: uuid.UUID) -> uuid.UUID:
    with SessionLocal() as db:
        entry = UserWardrobe(user_id=user_id, item_id=item_id)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry.wardrobe_id


def set_availability(item_id: uuid.UUID, availability: ClothingAvailability) -> None:
    with SessionLocal() as db:
        item = db.get(ClothingItem, item_id)
        item.availability_status = availability
        db.commit()


def get_balance(user_id: uuid.UUID) -> int:
    with SessionLocal() as db:
        return db.execute(select(User.coin_balance).where(User.user_id == user_id)).scalar_one()


def count_ledger(user_id: uuid.UUID) -> int:
    with SessionLocal() as db:
        return db.execute(
            select(func.count(CoinTransaction.transaction_id)).where(
                CoinTransaction.user_id == user_id
            )
        ).scalar_one()


def equipment_rows(avatar_id: uuid.UUID, slot: AvatarSlot | None = None) -> list[tuple]:
    """Plain-value snapshots so two calls are comparable (ORM instances
    compare by identity)."""
    with SessionLocal() as db:
        stmt = select(
            AvatarEquipment.slot, AvatarEquipment.item_id, AvatarEquipment.equipped_at
        ).where(AvatarEquipment.avatar_id == avatar_id)
        if slot is not None:
            stmt = stmt.where(AvatarEquipment.slot == slot)
        return db.execute(stmt).all()


def cleanup() -> None:
    with SessionLocal() as db:
        # coin_transactions.user_id is RESTRICT -> ledger rows go first.
        for username in (USER_A_NAME, USER_B_NAME, USER_C_NAME):
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is not None:
                for tx in db.execute(
                    select(CoinTransaction).where(CoinTransaction.user_id == user.user_id)
                ).scalars().all():
                    db.delete(tx)
                db.commit()
                # Deleting the user cascades avatar -> avatar_equipment and wardrobe.
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
        f"{len(created_category_ids)} categories and 3 test users."
    )


results = []
created_category_ids: list[int] = []
created_item_ids: list[uuid.UUID] = []

USER_A_NAME = f"eq_a_{RUN_ID}"
USER_B_NAME = f"eq_b_{RUN_ID}"
USER_C_NAME = f"eq_c_{RUN_ID}"  # avatar deleted -> simulates a LEGACY user

try:
    # --- Setup ---------------------------------------------------------------
    # Registration itself now creates each user's avatar — no manual
    # avatar inserts anywhere in this test.
    USER_A, AUTH_A = register_and_login(USER_A_NAME)
    USER_B, AUTH_B = register_and_login(USER_B_NAME)
    USER_C, AUTH_C = register_and_login(USER_C_NAME)

    with SessionLocal() as db:
        AVATAR_A = db.execute(select(Avatar.avatar_id).where(Avatar.user_id == USER_A)).scalar_one()
        AVATAR_B = db.execute(select(Avatar.avatar_id).where(Avatar.user_id == USER_B)).scalar_one()

    CATEGORY_IDS = {slot: add_category(slot) for slot in ALL_SLOTS}
    ITEM_IDS = {slot: add_item(CATEGORY_IDS[slot], f"it_{slot.value}_{RUN_ID}") for slot in ALL_SLOTS}
    SHIRT_A = add_item(CATEGORY_IDS[AvatarSlot.TOP], f"shirtA_{RUN_ID}")
    SHIRT_B = add_item(CATEGORY_IDS[AvatarSlot.TOP], f"shirtB_{RUN_ID}")

    W_A_TOP1 = grant_ownership(USER_A, ITEM_IDS[AvatarSlot.TOP])
    W_B_ITEM = grant_ownership(USER_B, ITEM_IDS[AvatarSlot.BOTTOM])

    def equip(wardrobe_id, headers=None):
        return client.post(f"/wardrobe/{wardrobe_id}/equip", headers=headers)

    def unequip(wardrobe_id, headers=None):
        return client.delete(f"/wardrobe/{wardrobe_id}/equip", headers=headers)

    # --- 1. Authentication -----------------------------------------------------
    results.append(("equip without token -> 401", equip(W_A_TOP1).status_code == 401, ""))
    results.append(("unequip without token -> 401", unequip(W_A_TOP1).status_code == 401, ""))
    results.append(
        ("equip with invalid token -> 401",
         equip(W_A_TOP1, {"Authorization": "Bearer not.a.jwt"}).status_code == 401, ""),
    )
    expired = {"Authorization": f"Bearer {make_token(str(USER_A), timedelta(minutes=-5))}"}
    results.append(("equip with expired token -> 401", equip(W_A_TOP1, expired).status_code == 401, ""))

    # --- 2. Missing avatar (legacy user) -> explicit 404 -------------------------
    # C owns an item (so the ownership check passes) but their avatar row
    # was removed to simulate a user registered before the avatar
    # lifecycle / backfill existed.
    delete_avatar(USER_C)
    W_C_ITEM = grant_ownership(USER_C, ITEM_IDS[AvatarSlot.ACCESSORY])
    r = equip(W_C_ITEM, AUTH_C)
    results.append(
        ("legacy user without avatar -> 404 'Avatar not found'",
         r.status_code == 404 and r.json()["detail"] == "Avatar not found",
         f"{r.status_code} {r.json().get('detail')}"),
    )

    # --- 3. Successful equip --------------------------------------------------------
    balance_before = get_balance(USER_A)
    ledger_before = count_ledger(USER_A)
    r = equip(W_A_TOP1, AUTH_A)
    body = r.json()
    ok = (
        r.status_code == 200
        and set(body.keys()) == SAFE_EQUIP_FIELDS
        and body["message"] == "Item equipped successfully"
        and body["equipment"]["avatar_id"] == str(AVATAR_A)
        and body["equipment"]["slot"] == "top"
        and body["equipment"]["item"]["item_id"] == str(ITEM_IDS[AvatarSlot.TOP])
        and body["equipment"]["equipped_at"] is not None
    )
    results.append(("valid equip -> 200 with whitelisted fields", ok, str(body)))

    rows = equipment_rows(AVATAR_A, AvatarSlot.TOP)
    results.append(
        ("DB has exactly one equipment row with correct values",
         len(rows) == 1
         and rows[0][1] == ITEM_IDS[AvatarSlot.TOP]
         and rows[0][2] is not None,
         f"{len(rows)} row(s)"),
    )

    item_payload = body["equipment"]["item"]
    results.append(
        ("equipped item matches catalog shape",
         set(item_payload.keys()) == SAFE_ITEM_FIELDS
         and set(item_payload["category"].keys()) == SAFE_CATEGORY_FIELDS
         and item_payload["category"]["slot"] == "top",
         ""),
    )

    # --- 4. Ownership isolation ---------------------------------------------------------
    a_rows_before = equipment_rows(AVATAR_A)
    b_rows_before = equipment_rows(AVATAR_B)

    r = equip(W_B_ITEM, AUTH_A)          # A tries to equip B's bottom item
    results.append(
        ("user A equipping user B's wardrobe_id -> 404",
         r.status_code == 404 and r.json()["detail"] == "Wardrobe item not found",
         str(r.status_code)),
    )
    results.append(
        ("A's attempt changed neither A's nor B's equipment",
         equipment_rows(AVATAR_A) == a_rows_before and equipment_rows(AVATAR_B) == b_rows_before,
         ""),
    )

    r = unequip(W_B_ITEM, AUTH_A)        # A tries to unequip B's bottom item
    results.append(
        ("user A unequipping user B's wardrobe_id -> 404",
         r.status_code == 404 and r.json()["detail"] == "Wardrobe item not found",
         str(r.status_code)),
    )
    results.append(
        ("B's equipment untouched by A's unequip attempt",
         equipment_rows(AVATAR_B) == b_rows_before, ""),
    )

    # --- 5. Slot determination across ALL six categories -----------------------------------
    # TOP is already owned by A (W_A_TOP1); the other five slots get fresh grants.
    for slot in ALL_SLOTS:
        wid = W_A_TOP1 if slot == AvatarSlot.TOP else grant_ownership(USER_A, ITEM_IDS[slot])
        r = equip(wid, AUTH_A)
        rows = equipment_rows(AVATAR_A, slot)
        results.append(
            (f"slot {slot.value}: item lands in its category's slot",
             r.status_code == 200
             and r.json()["equipment"]["slot"] == slot.value
             and len(rows) == 1
             and rows[0][1] == ITEM_IDS[slot],
             str(r.status_code)),
        )

    # --- 6. Replacement within one slot ------------------------------------------------------
    W_SHIRT_A = grant_ownership(USER_A, SHIRT_A)
    W_SHIRT_B = grant_ownership(USER_A, SHIRT_B)
    equip(W_SHIRT_A, AUTH_A)
    r = equip(W_SHIRT_B, AUTH_A)
    rows = equipment_rows(AVATAR_A, AvatarSlot.TOP)
    with SessionLocal() as db:
        shirt_a_still_owned = db.execute(
            select(func.count(UserWardrobe.wardrobe_id)).where(
                UserWardrobe.user_id == USER_A, UserWardrobe.item_id == SHIRT_A
            )
        ).scalar_one()
    results.append(
        ("equipping over an occupied slot replaces it",
         r.status_code == 200
         and len(rows) == 1
         and rows[0][1] == SHIRT_B
         and shirt_a_still_owned == 1,
         f"rows={len(rows)} shirt_a_owned={shirt_a_still_owned}"),
    )

    # --- 7. Multiple slots coexist --------------------------------------------------------------
    top_row = [row for row in equipment_rows(AVATAR_A) if row.slot == AvatarSlot.TOP]
    distinct_slots = {row.slot for row in equipment_rows(AVATAR_A)}
    results.append(
        ("TOP + BOTTOM + SHOES (+ others) coexist on one avatar",
         len(top_row) == 1
         and {AvatarSlot.TOP, AvatarSlot.BOTTOM, AvatarSlot.SHOES}.issubset(distinct_slots),
         f"slots={sorted(s.value for s in distinct_slots)}"),
    )

    # --- 8. Unequip ---------------------------------------------------------------------------------
    r = unequip(W_SHIRT_B, AUTH_A)
    rows = equipment_rows(AVATAR_A, AvatarSlot.TOP)
    with SessionLocal() as db:
        still_owned = db.execute(
            select(func.count(UserWardrobe.wardrobe_id)).where(
                UserWardrobe.user_id == USER_A, UserWardrobe.item_id == SHIRT_B
            )
        ).scalar_one()
    results.append(
        ("unequip removes the equipment row, keeps ownership",
         r.status_code == 200
         and set(r.json().keys()) == SAFE_UNEQUIP_FIELDS
         and r.json()["slot"] == "top"
         and len(rows) == 0
         and still_owned == 1,
         f"rows={len(rows)} owned={still_owned}"),
    )

    # --- 9. Incorrect unequip ---------------------------------------------------------------------------
    equip(W_SHIRT_A, AUTH_A)             # TOP -> Shirt A
    r = unequip(W_SHIRT_B, AUTH_A)       # ask to unequip Shirt B instead
    rows = equipment_rows(AVATAR_A, AvatarSlot.TOP)
    results.append(
        ("unequipping a DIFFERENT item -> 409, Shirt A stays equipped",
         r.status_code == 409
         and r.json()["detail"] == "This item is not currently equipped"
         and len(rows) == 1
         and rows[0][1] == SHIRT_A,
         f"{r.status_code} rows={len(rows)}"),
    )

    # Unequipping something never equipped at all -> same 409.
    r = unequip(W_A_TOP1, AUTH_A)        # was replaced earlier, not equipped now
    results.append(
        ("unequipping an un-equipped owned item -> 409",
         r.status_code == 409 and len(equipment_rows(AVATAR_A, AvatarSlot.TOP)) == 1,
         str(r.status_code)),
    )

    # --- 10. Availability independence --------------------------------------------------------------------
    set_availability(SHIRT_A, ClothingAvailability.UNAVAILABLE)
    rows = equipment_rows(AVATAR_A, AvatarSlot.TOP)
    results.append(
        ("admin marking the equipped item UNAVAILABLE keeps it equipped",
         len(rows) == 1 and rows[0][1] == SHIRT_A, ""),
    )
    r = unequip(W_SHIRT_A, AUTH_A)       # still manageable
    results.append(
        ("UNAVAILABLE but owned item can still be unequipped",
         r.status_code == 200 and len(equipment_rows(AVATAR_A, AvatarSlot.TOP)) == 0,
         str(r.status_code)),
    )
    set_availability(SHIRT_A, ClothingAvailability.AVAILABLE)

    # --- 11. Coin safety -------------------------------------------------------------------------------------
    results.append(
        ("coin_balance unchanged by equip/unequip",
         get_balance(USER_A) == balance_before,
         f"before={balance_before} after={get_balance(USER_A)}"),
    )
    results.append(
        ("no coin_transactions rows created by equip/unequip",
         count_ledger(USER_A) == ledger_before,
         f"ledger={count_ledger(USER_A)}"),
    )

    # --- 12. Not found / malformed ------------------------------------------------------------------------------
    bogus = uuid.uuid4()
    results.append(("equip nonexistent wardrobe_id -> 404", equip(bogus, AUTH_A).status_code == 404, ""))
    results.append(("unequip nonexistent wardrobe_id -> 404", unequip(bogus, AUTH_A).status_code == 404, ""))
    results.append(("malformed wardrobe_id (equip) -> 422", equip("not-a-uuid", AUTH_A).status_code == 422, ""))
    results.append(("malformed wardrobe_id (unequip) -> 422", unequip("not-a-uuid", AUTH_A).status_code == 422, ""))

    # --- 13. Data safety ------------------------------------------------------------------------------------------
    raw = client.post(f"/wardrobe/{W_SHIRT_B}/equip", headers=AUTH_A).text.lower()
    leaks = [frag for frag in ("password", "email", "is_admin", "coin_balance", "auth_provider") if frag in raw]
    results.append(("no sensitive fields anywhere in equip payload", not leaks, str(leaks) or "clean"))

    # --- 14. Concurrency: two simultaneous equips into the SAME slot -----------------------------------------------
    barrier = threading.Barrier(2)
    statuses = []

    def http_race():
        barrier.wait()
        statuses.append(equip(W_SHIRT_A if threading.current_thread().name.endswith("_1") else W_SHIRT_B, AUTH_A).status_code)

    threads = [threading.Thread(target=http_race, name=f"equip_race_{i}") for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = equipment_rows(AVATAR_A, AvatarSlot.TOP)
    ok = (
        sorted(statuses) == [200, 200]
        and len(rows) == 1
        and rows[0][1] in (SHIRT_A, SHIRT_B)
    )
    results.append(
        ("concurrent equips of two shirts into TOP -> exactly one valid row",
         ok, f"statuses={statuses} rows={len(rows)} winner={rows[0][1] if rows else None}"),
    )

finally:
    cleanup()

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)
