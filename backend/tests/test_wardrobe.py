"""
End-to-end checks of the wardrobe listing endpoint (GET /wardrobe) —
Phase 4, part 1 (read-only; equip/unequip comes later).

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_wardrobe

Covers:
  - Authentication: missing / invalid / expired token -> 401; an
    authenticated user gets 200.
  - Empty wardrobe: a user who never purchased -> 200 with items=[] and
    total=0 (an empty closet is valid, never a 404).
  - Single item: a real purchase surfaces as one entry with the correct
    wardrobe_id, purchased_at, and the full catalog item shape.
  - Multiple items + ordering: entries come back newest-purchase-first
    (purchased_at DESC, wardrobe_id DESC tiebreaker) and repeated
    requests return an identical sequence.
  - Pagination: limit/offset tile the wardrobe without overlap or gaps;
    offset past the end -> empty page with total intact; invalid
    limit/offset -> 422; maximum limit accepted.
  - User isolation (mandatory): two users with disjoint wardrobes each
    see ONLY their own rows; a client-supplied user_id query parameter
    cannot redirect the query at someone else's wardrobe.
  - Availability independence: an owned item whose availability_status
    an admin later flips to UNAVAILABLE stays visible in the wardrobe
    (availability governs buying, not owning).
  - Data safety: the envelope exposes only items/total/limit/offset;
    each entry only wardrobe_id/purchased_at/item; the item matches the
    public catalog shape; no email/password hash/auth-provider/internal
    fields appear anywhere in the payload.

The test creates its OWN categories/items/users (names carry RUN_ID) so
it is isolated from the seeded catalog and any leftovers; all created
rows are deleted afterwards.
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

# Response field whitelists.
SAFE_ENVELOPE_FIELDS = {"items", "total", "limit", "offset"}
SAFE_ENTRY_FIELDS = {"wardrobe_id", "purchased_at", "item"}
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
SAFE_CATEGORY_FIELDS = {"category_id", "category_name", "slot"}

PRICE = 50


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
    client.post(
        "/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
    )
    login = client.post("/auth/login", json={"username": username, "password": PASSWORD})
    token = login.json().get("access_token", "")
    headers = {"Authorization": f"Bearer {token}"}
    with SessionLocal() as db:
        user_id = db.execute(select(User.user_id).where(User.username == username)).scalar_one()
    return user_id, headers


def add_category(name: str) -> int:
    with SessionLocal() as db:
        category = ClothingCategory(category_name=name, slot=AvatarSlot.ACCESSORY)
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
            price=PRICE,
            image_url=f"https://example.com/{name}.png",
            availability_status=availability,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        created_item_ids.append(item.item_id)
        return item.item_id


def grant_ownership(user_id: uuid.UUID, item_id: uuid.UUID, purchased_at: datetime) -> uuid.UUID:
    """Insert a user_wardrobe row directly with a CONTROLLED timestamp so
    ordering assertions are exact regardless of commit timing."""
    with SessionLocal() as db:
        entry = UserWardrobe(user_id=user_id, item_id=item_id, purchased_at=purchased_at)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry.wardrobe_id


def set_availability(item_id: uuid.UUID, availability: ClothingAvailability) -> None:
    with SessionLocal() as db:
        item = db.get(ClothingItem, item_id)
        item.availability_status = availability
        db.commit()


def cleanup() -> None:
    with SessionLocal() as db:
        # coin_transactions.user_id is RESTRICT -> ledger rows go first.
        for username in (USER_A_NAME, USER_B_NAME):
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is not None:
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
        f"{len(created_category_ids)} categories and 2 test users."
    )


results = []
created_category_ids: list[int] = []
created_item_ids: list[uuid.UUID] = []

USER_A_NAME = f"ward_a_{RUN_ID}"
USER_B_NAME = f"ward_b_{RUN_ID}"

try:
    # --- Setup: two users + one private category with test items ----------
    USER_A, AUTH_A = register_and_login(USER_A_NAME)
    USER_B, AUTH_B = register_and_login(USER_B_NAME)
    results.append(("setup: both users registered + logged in", bool(AUTH_A["Authorization"]) and bool(AUTH_B["Authorization"]), ""))

    CATEGORY = add_category(f"W_{RUN_ID}")
    ITEM_1 = add_item(CATEGORY, f"w1_{RUN_ID}")
    ITEM_2 = add_item(CATEGORY, f"w2_{RUN_ID}")
    ITEM_3 = add_item(CATEGORY, f"w3_{RUN_ID}")
    ITEM_4 = add_item(CATEGORY, f"w4_{RUN_ID}")
    ITEM_B1 = add_item(CATEGORY, f"wb1_{RUN_ID}")

    # --- 1. Authentication ---------------------------------------------------
    results.append(("wardrobe without token -> 401", client.get("/wardrobe").status_code == 401, ""))
    results.append(
        ("wardrobe with invalid token -> 401",
         client.get("/wardrobe", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401, ""),
    )
    expired = {"Authorization": f"Bearer {make_token(str(USER_A), timedelta(minutes=-5))}"}
    results.append(("wardrobe with expired token -> 401", client.get("/wardrobe", headers=expired).status_code == 401, ""))
    r = client.get("/wardrobe", headers=AUTH_A)
    results.append(("wardrobe with valid token -> 200", r.status_code == 200, str(r.status_code)))

    # --- 2. Empty wardrobe ------------------------------------------------------
    body = r.json()
    results.append(
        ("empty wardrobe -> 200, [], total=0",
         r.status_code == 200 and body["items"] == [] and body["total"] == 0,
         f"total={body['total']}"),
    )

    # --- 3. Single item via a REAL purchase ---------------------------------------
    set_balance_ok = True
    with SessionLocal() as db:
        user = db.get(User, USER_A)
        user.coin_balance = PRICE
        db.commit()
    purchase = client.post(f"/clothing/{ITEM_1}/purchase", headers=AUTH_A)
    results.append(("setup: real purchase succeeded", purchase.status_code == 200, str(purchase.status_code)))
    purchase_body = purchase.json()

    r = client.get("/wardrobe", headers=AUTH_A)
    body = r.json()
    ok = (
        r.status_code == 200
        and body["total"] == 1
        and len(body["items"]) == 1
        and body["items"][0]["wardrobe_id"] == purchase_body["wardrobe_id"]
        and body["items"][0]["item"]["item_id"] == str(ITEM_1)
        and body["items"][0]["item"]["price"] == PRICE
    )
    results.append(("single owned item appears with correct ids", ok, f"total={body['total']}"))

    with SessionLocal() as db:
        db_row = db.execute(
            select(UserWardrobe).where(
                UserWardrobe.user_id == USER_A, UserWardrobe.item_id == ITEM_1
            )
        ).scalar_one()
    results.append(
        ("purchased_at matches the DB row",
         body["items"][0]["purchased_at"] is not None
         and datetime.fromisoformat(body["items"][0]["purchased_at"].replace("Z", "+00:00")).timestamp()
         == db_row.purchased_at.timestamp(),
         body["items"][0]["purchased_at"]),
    )

    # --- 4. Multiple items + deterministic ordering -------------------------------
    # Controlled timestamps in the PAST (the real purchase above happened
    # "now", so it is the newest): base, base+10m, base+20m.
    base = datetime(2026, 1, 1, 12, 0, 0)
    W2 = grant_ownership(USER_A, ITEM_2, base)
    W4 = grant_ownership(USER_A, ITEM_4, base + timedelta(minutes=20))
    W3 = grant_ownership(USER_A, ITEM_3, base + timedelta(minutes=10))
    # Expected order: the real purchase (now), then W4, W3, W2.
    expected_order = [uuid.UUID(purchase_body["wardrobe_id"]), W4, W3, W2]

    r1 = client.get("/wardrobe", headers=AUTH_A, params={"limit": 100})
    got = [uuid.UUID(e["wardrobe_id"]) for e in r1.json()["items"]]
    results.append(("entries ordered newest-purchase-first", got == expected_order, str(got)))

    r2 = client.get("/wardrobe", headers=AUTH_A, params={"limit": 100})
    results.append(
        ("repeated requests return identical order",
         [e["wardrobe_id"] for e in r1.json()["items"]] == [e["wardrobe_id"] for e in r2.json()["items"]], ""),
    )

    # --- 5. Pagination ---------------------------------------------------------------
    r = client.get("/wardrobe", headers=AUTH_A, params={"limit": 2})
    body = r.json()
    results.append(
        ("limit=2 -> 2 items, total=4",
         len(body["items"]) == 2 and body["total"] == 4,
         f"items={len(body['items'])} total={body['total']}"),
    )

    p1 = client.get("/wardrobe", headers=AUTH_A, params={"limit": 2, "offset": 0}).json()
    p2 = client.get("/wardrobe", headers=AUTH_A, params={"limit": 2, "offset": 2}).json()
    p1_ids = {e["wardrobe_id"] for e in p1["items"]}
    p2_ids = {e["wardrobe_id"] for e in p2["items"]}
    results.append(
        ("offset=2 continues without overlap",
         len(p1["items"]) == 2 and len(p2["items"]) == 2 and not (p1_ids & p2_ids),
         f"p1={len(p1['items'])} p2={len(p2['items'])}"),
    )

    tiled = [e["wardrobe_id"] for e in p1["items"]] + [e["wardrobe_id"] for e in p2["items"]]
    results.append(("pages cover the wardrobe exactly", tiled == [str(w) for w in expected_order], ""))

    past_end = client.get("/wardrobe", headers=AUTH_A, params={"limit": 2, "offset": 100}).json()
    results.append(
        ("offset past the end -> empty items, total intact",
         past_end["items"] == [] and past_end["total"] == 4, ""),
    )

    max_page = client.get("/wardrobe", headers=AUTH_A, params={"limit": 100})
    results.append(("maximum limit (100) accepted", max_page.status_code == 200, str(max_page.status_code)))

    for bad in [{"limit": 0}, {"limit": -1}, {"limit": 101}, {"limit": "abc"}, {"offset": -1}, {"offset": "abc"}]:
        resp = client.get("/wardrobe", headers=AUTH_A, params=bad)
        results.append((f"invalid pagination {bad} -> 422", resp.status_code == 422, str(resp.status_code)))

    # --- 6. User isolation (mandatory) --------------------------------------------------
    # Give B one item A does not own.
    grant_ownership(USER_B, ITEM_B1, base)

    a_view = client.get("/wardrobe", headers=AUTH_A, params={"limit": 100}).json()
    b_view = client.get("/wardrobe", headers=AUTH_B, params={"limit": 100}).json()
    a_items = {e["item"]["item_id"] for e in a_view["items"]}
    b_items = {e["item"]["item_id"] for e in b_view["items"]}
    results.append(
        ("user A sees only A's items",
         a_items == {str(i) for i in (ITEM_1, ITEM_2, ITEM_3, ITEM_4)} and str(ITEM_B1) not in a_items,
         f"a_total={a_view['total']}"),
    )
    results.append(
        ("user B sees only B's items",
         b_items == {str(ITEM_B1)} and b_view["total"] == 1,
         f"b_total={b_view['total']}"),
    )

    # No user_id input exists; a client-supplied one must be ignored entirely.
    smuggled = client.get("/wardrobe", headers=AUTH_A, params={"user_id": str(USER_B), "limit": 100}).json()
    results.append(
        ("client-supplied user_id query param ignored",
         {e["item"]["item_id"] for e in smuggled["items"]} == a_items and smuggled["total"] == a_view["total"],
         f"total={smuggled['total']}"),
    )

    # --- 7. Availability independence -----------------------------------------------------
    set_availability(ITEM_1, ClothingAvailability.UNAVAILABLE)
    set_availability(ITEM_2, ClothingAvailability.UPCOMING)
    r = client.get("/wardrobe", headers=AUTH_A, params={"limit": 100})
    statuses = {e["item"]["item_id"]: e["item"]["availability_status"] for e in r.json()["items"]}
    results.append(
        ("owned UNAVAILABLE/UPCOMING items stay visible",
         r.json()["total"] == 4
         and statuses[str(ITEM_1)] == "unavailable"
         and statuses[str(ITEM_2)] == "upcoming",
         str(statuses)),
    )
    # Restore so cleanup deletes cleanly and later sections see sane state.
    set_availability(ITEM_1, ClothingAvailability.AVAILABLE)
    set_availability(ITEM_2, ClothingAvailability.AVAILABLE)

    # --- 8. Data safety --------------------------------------------------------------------
    raw = client.get("/wardrobe", headers=AUTH_A, params={"limit": 100}).text
    envelope_offenders = set(r1.json().keys()) - SAFE_ENVELOPE_FIELDS
    results.append(("envelope exposes only safe fields", not envelope_offenders, str(sorted(envelope_offenders)) or "clean"))

    entry_offenders = {k for e in r1.json()["items"] for k in e.keys()} - SAFE_ENTRY_FIELDS
    results.append(("entry exposes only safe fields", not entry_offenders, str(sorted(entry_offenders)) or "clean"))

    item_offenders = {k for e in r1.json()["items"] for k in e["item"].keys()} - SAFE_ITEM_FIELDS
    results.append(("nested item matches the catalog shape", not item_offenders, str(sorted(item_offenders)) or "clean"))

    category_offenders = {k for e in r1.json()["items"] for k in e["item"]["category"].keys()} - SAFE_CATEGORY_FIELDS
    results.append(("nested category exposes only safe fields", not category_offenders, str(sorted(category_offenders)) or "clean"))

    sensitive_fragments = [
        USER_A_NAME.lower(),          # username itself must not leak either
        "password",
        "password_hash",
        "email",
        "auth_provider",
        "is_active",
        "coin_balance",
        "is_admin",
    ]
    leaks = [frag for frag in sensitive_fragments if frag in raw.lower()]
    results.append(("no sensitive user/auth fields anywhere in payload", not leaks, str(leaks) or "clean"))

finally:
    cleanup()

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)
