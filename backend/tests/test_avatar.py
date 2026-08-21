"""
End-to-end checks of the avatar retrieval endpoint (GET /avatar) —
Phase 5.

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_avatar

Covers:
  - Authentication: missing / invalid / expired token -> 401; an
    authenticated user gets 200.
  - Registration creates the avatar: every newly registered user has
    exactly one avatar (avatars.user_id is UNIQUE) before any other
    call, so GET /avatar succeeds immediately after register+login.
  - Missing avatar: only reachable for a LEGACY user whose avatar row
    is absent (pre-backfill data, simulated here by deleting the row);
    such a user gets an explicit 404 "Avatar not found" (no silent
    avatar creation inside a read endpoint).
  - One-avatar-per-user: inserting a second avatar for the same user
    violates the avatars.user_id UNIQUE constraint.
  - Empty equipment: an avatar with nothing equipped -> 200 with ALL
    SIX slots present and explicitly null (never missing keys).
  - One equipped item: after equipping a TOP item through the real
    wardrobe equip endpoint, TOP holds exactly that item (correct
    item_id/name/image_url/category.slot/equipped_at) and every other
    slot stays null.
  - Multiple equipped items: items equipped into all six slots appear
    each in THEIR OWN slot and never in another slot's position.
  - Replacement: equipping Shirt B over Shirt A in TOP makes the avatar
    report Shirt B only — Shirt A's id appears nowhere in the payload.
  - User isolation (mandatory): two users with differently equipped
    avatars each see ONLY their own avatar_id and equipment, in both
    directions; a client-supplied user_id query parameter cannot
    redirect the query at someone else's avatar.
  - Availability independence: an equipped item an admin later marks
    UNAVAILABLE stays visible in the avatar (availability governs
    buying, not wearing).
  - Data safety: the envelope exposes only avatar_id/equipment; the
    equipment map exposes exactly the six slot keys; an occupied slot
    exposes only equipped_at/item; the item matches the public catalog
    shape; no email/password hash/auth-provider/balance/admin/streak
    fields appear anywhere in the payload.

The test creates its OWN users/categories/items (names carry RUN_ID) so
it is isolated from seeded data; everything is deleted afterwards.
Avatars come from registration itself; equipment is placed exclusively
through the existing wardrobe equip endpoint, so Phase 4 -> Phase 5
integration is exercised end to end.
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
    Avatar,
    AvatarSlot,
    ClothingAvailability,
    ClothingCategory,
    ClothingItem,
    User,
    UserWardrobe,
)

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

# Response field whitelists.
SAFE_AVATAR_FIELDS = {"avatar_id", "equipment"}
SAFE_EQUIPMENT_KEYS = {"hair", "hat", "top", "bottom", "shoes", "accessory"}
SAFE_SLOT_FIELDS = {"equipped_at", "item"}
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


def equip(wardrobe_id: uuid.UUID, headers: dict) -> None:
    """Place equipment through the REAL wardrobe endpoint."""
    r = client.post(f"/wardrobe/{wardrobe_id}/equip", headers=headers)
    assert r.status_code == 200, f"equip failed: {r.status_code} {r.text}"


def set_availability(item_id: uuid.UUID, availability: ClothingAvailability) -> None:
    with SessionLocal() as db:
        item = db.get(ClothingItem, item_id)
        item.availability_status = availability
        db.commit()


def get_avatar(headers: dict, query: str = "") -> dict:
    return client.get(f"/avatar{query}", headers=headers)


def cleanup() -> None:
    with SessionLocal() as db:
        # Deleting the user cascades avatar -> avatar_equipment and wardrobe.
        for username in (USER_A_NAME, USER_B_NAME, USER_C_NAME):
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is not None:
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

USER_A_NAME = f"av_a_{RUN_ID}"
USER_B_NAME = f"av_b_{RUN_ID}"
USER_C_NAME = f"av_c_{RUN_ID}"  # avatar deleted -> simulates a LEGACY user

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
        count_c = db.execute(
            select(Avatar.avatar_id).where(Avatar.user_id == USER_C)
        ).scalars().all()

    # --- 0. Registration-created avatars (the fixed lifecycle) -------------------
    results.append(
        ("registration created exactly one avatar for each new user",
         len({USER_A, USER_B, USER_C}) == 3 and len(count_c) == 1,
         f"user C avatars={len(count_c)}"),
    )

    # One-avatar-per-user: a second avatar for the same user must violate
    # the avatars.user_id UNIQUE constraint at the database level.
    try:
        with SessionLocal() as db:
            db.add(Avatar(user_id=USER_A))
            db.commit()
        unique_violation = False
    except Exception:
        unique_violation = True
    results.append(
        ("second avatar for the same user rejected by UNIQUE(user_id)",
         unique_violation, ""),
    )

    # GET /avatar works immediately after register+login (no extra setup).
    r = get_avatar(AUTH_C)
    results.append(
        ("GET /avatar succeeds right after registration",
         r.status_code == 200 and r.json()["avatar_id"] == str(count_c[0]),
         str(r.status_code)),
    )

    # Now strip user C's avatar to simulate the legacy pre-fix state.
    delete_avatar(USER_C)

    CATEGORY_IDS = {slot: add_category(slot) for slot in ALL_SLOTS}
    ITEM_IDS = {slot: add_item(CATEGORY_IDS[slot], f"it_{slot.value}_{RUN_ID}") for slot in ALL_SLOTS}
    SHIRT_A = add_item(CATEGORY_IDS[AvatarSlot.TOP], f"shirtA_{RUN_ID}")
    SHIRT_B = add_item(CATEGORY_IDS[AvatarSlot.TOP], f"shirtB_{RUN_ID}")
    B_BOTTOM = add_item(CATEGORY_IDS[AvatarSlot.BOTTOM], f"b_bottom_{RUN_ID}")

    # --- 1. Authentication -----------------------------------------------------
    results.append(("avatar without token -> 401", client.get("/avatar").status_code == 401, ""))
    results.append(
        ("avatar with invalid token -> 401",
         client.get("/avatar", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401, ""),
    )
    expired = {"Authorization": f"Bearer {make_token(str(USER_A), timedelta(minutes=-5))}"}
    results.append(("avatar with expired token -> 401", client.get("/avatar", headers=expired).status_code == 401, ""))

    # --- 2. Missing avatar (legacy user) -> explicit 404 -------------------------
    r = get_avatar(AUTH_C)
    results.append(
        ("legacy user without avatar -> 404 'Avatar not found'",
         r.status_code == 404 and r.json()["detail"] == "Avatar not found",
         f"{r.status_code} {r.json().get('detail')}"),
    )

    # --- 3. Empty equipment --------------------------------------------------------
    r = get_avatar(AUTH_A)
    body = r.json()
    ok = (
        r.status_code == 200
        and set(body.keys()) == SAFE_AVATAR_FIELDS
        and body["avatar_id"] == str(AVATAR_A)
        and set(body["equipment"].keys()) == SAFE_EQUIPMENT_KEYS
        and all(body["equipment"][slot] is None for slot in SAFE_EQUIPMENT_KEYS)
    )
    results.append(("empty avatar -> 200, correct avatar_id, all six slots null", ok, str(body)))

    # --- 4. One equipped item ---------------------------------------------------------
    W_TOP = grant_ownership(USER_A, ITEM_IDS[AvatarSlot.TOP])
    equip(W_TOP, AUTH_A)

    r = get_avatar(AUTH_A)
    body = r.json()
    top = body["equipment"]["top"]
    others_empty = all(body["equipment"][s] is None for s in SAFE_EQUIPMENT_KEYS - {"top"})
    ok = (
        r.status_code == 200
        and body["avatar_id"] == str(AVATAR_A)
        and top is not None
        and set(top.keys()) == SAFE_SLOT_FIELDS
        and top["equipped_at"] is not None
        and top["item"]["item_id"] == str(ITEM_IDS[AvatarSlot.TOP])
        and top["item"]["name"] == f"it_top_{RUN_ID}"
        and top["item"]["image_url"] == f"https://example.com/it_top_{RUN_ID}.png"
        and top["item"]["category"]["slot"] == "top"
        and others_empty
    )
    results.append(("one equipped TOP item -> correct slot/item, other slots null", ok, str(body)))

    # --- 5. Multiple equipped items across all six slots -----------------------------------
    for slot in ALL_SLOTS:
        if slot == AvatarSlot.TOP:
            continue
        wid = grant_ownership(USER_A, ITEM_IDS[slot])
        equip(wid, AUTH_A)

    r = get_avatar(AUTH_A)
    body = r.json()
    ok = r.status_code == 200
    wrong_slots = []
    for slot in ALL_SLOTS:
        entry = body["equipment"][slot.value]
        if entry is None or entry["item"]["item_id"] != str(ITEM_IDS[slot]):
            wrong_slots.append(slot.value)
    ok = ok and not wrong_slots
    results.append(
        ("all six slots equipped -> each item in ITS OWN slot, none misplaced",
         ok, f"wrong={wrong_slots}" if wrong_slots else ""),
    )

    # --- 6. Replacement within one slot ------------------------------------------------------
    W_SHIRT_A = grant_ownership(USER_A, SHIRT_A)
    W_SHIRT_B = grant_ownership(USER_A, SHIRT_B)
    equip(W_SHIRT_A, AUTH_A)
    r = get_avatar(AUTH_A)
    results.append(
        ("TOP -> Shirt A reported before replacement",
         r.json()["equipment"]["top"]["item"]["item_id"] == str(SHIRT_A), ""),
    )

    equip(W_SHIRT_B, AUTH_A)
    r = get_avatar(AUTH_A)
    raw = r.text
    ok = (
        r.status_code == 200
        and r.json()["equipment"]["top"]["item"]["item_id"] == str(SHIRT_B)
        and str(SHIRT_A) not in raw
    )
    results.append(
        ("TOP -> Shirt B after replacement; Shirt A nowhere in payload", ok, ""),
    )

    # --- 7. User isolation -----------------------------------------------------------------------
    W_B_BOTTOM = grant_ownership(USER_B, B_BOTTOM)
    equip(W_B_BOTTOM, AUTH_B)

    r_a = get_avatar(AUTH_A)
    body_a = r_a.json()
    # A wears the five non-TOP seeded items plus Shirt B in top; B's bottom
    # item must appear nowhere in A's payload.
    still_worn = [iid for slot, iid in ITEM_IDS.items() if slot != AvatarSlot.TOP]
    ok_a = (
        r_a.status_code == 200
        and body_a["avatar_id"] == str(AVATAR_A)
        and body_a["equipment"]["top"]["item"]["item_id"] == str(SHIRT_B)
        and str(B_BOTTOM) not in r_a.text
        and all(str(iid) in r_a.text for iid in still_worn)
    )
    results.append(
        ("user A sees ONLY avatar A with A's equipment (no B items)", ok_a, str(body_a["avatar_id"])),
    )

    r_b = get_avatar(AUTH_B)
    body_b = r_b.json()
    ok_b = (
        r_b.status_code == 200
        and body_b["avatar_id"] == str(AVATAR_B)
        and body_b["equipment"]["bottom"]["item"]["item_id"] == str(B_BOTTOM)
        and body_b["equipment"]["top"] is None
        and not any(str(iid) in r_b.text for iid in ITEM_IDS.values())
        and str(SHIRT_B) not in r_b.text
    )
    results.append(
        ("user B sees ONLY avatar B with B's equipment (no A items)", ok_b, str(body_b["avatar_id"])),
    )

    r_smuggle = get_avatar(AUTH_A, f"?user_id={USER_B}&avatar_id={AVATAR_B}")
    results.append(
        ("smuggled ?user_id/?avatar_id params are ignored -> still A's avatar",
         r_smuggle.status_code == 200 and r_smuggle.json()["avatar_id"] == str(AVATAR_A),
         str(r_smuggle.json().get("avatar_id"))),
    )

    # --- 8. Availability independence ---------------------------------------------------------------
    set_availability(SHIRT_B, ClothingAvailability.UNAVAILABLE)
    r = get_avatar(AUTH_A)
    top = r.json()["equipment"]["top"]
    results.append(
        ("equipped item marked UNAVAILABLE stays visible on the avatar",
         r.status_code == 200
         and top is not None
         and top["item"]["item_id"] == str(SHIRT_B)
         and top["item"]["availability_status"] == "unavailable",
         str(top and top["item"]["availability_status"])),
    )

    # --- 9. Data safety ------------------------------------------------------------------------------
    r = get_avatar(AUTH_A)
    body = r.json()
    occupied_slots = [k for k, v in body["equipment"].items() if v is not None]
    shapes_ok = (
        set(body.keys()) == SAFE_AVATAR_FIELDS
        and set(body["equipment"].keys()) == SAFE_EQUIPMENT_KEYS
        and all(set(body["equipment"][k].keys()) == SAFE_SLOT_FIELDS for k in occupied_slots)
        and all(
            set(body["equipment"][k]["item"].keys()) == SAFE_ITEM_FIELDS
            and set(body["equipment"][k]["item"]["category"].keys()) == SAFE_CATEGORY_FIELDS
            for k in occupied_slots
        )
    )
    results.append(("payload exposes exactly the whitelisted fields at every level", shapes_ok, ""))

    leaks = [
        frag
        for frag in ("password", "email", "is_admin", "coin_balance", "auth_provider",
                     "winning_streak", "biography", "username")
        if frag in r.text.lower()
    ]
    results.append(("no sensitive fields anywhere in the avatar payload", not leaks, str(leaks) or "clean"))

finally:
    cleanup()

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)
