"""
End-to-end checks of the clothing shop browsing endpoint (GET /clothing).

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_clothing_browse

Covers:
  - Authentication: unauthenticated / invalid / expired token -> 401;
    an authenticated user is granted access.
  - Listing: deterministic ordering (created_at DESC, item_id DESC — a
    repeated request returns an identical sequence), limit/offset
    pagination that tiles the whole result set with no overlap, and a
    `total` that reflects the AVAILABLE count under the active filter.
  - Filtering: filtering by a real category id returns only that
    category's items; a category with no AVAILABLE items returns an
    empty page; a nonexistent-but-in-range category id -> 404.
  - Availability: UNAVAILABLE and UPCOMING items never appear in browse
    results (the shop shelf is AVAILABLE-only).
  - Validation: non-integer / out-of-SMALLINT-range / non-positive
    category_id, limit out of 1-100, and a negative offset all -> 422.
  - Data safety: every item exposes only the whitelisted public fields
    and never internal/FK housekeeping.

The test creates its OWN categories and items (names carry RUN_ID) so it
is isolated from the seeded catalog and any leftovers; all created rows
are deleted afterwards.
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
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import AvatarSlot, ClothingAvailability, ClothingCategory, ClothingItem, User

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
USERNAME = f"browse_{RUN_ID}"
PASSWORD = "SuperSecret123!"

# Every field the browse API is allowed to expose for an item.
SAFE_FIELDS = {
    "item_id",
    "name",
    "description",
    "category",
    "price",
    "image_url",
    "availability_status",
    "collection_id",
}
# The nested category object's own whitelist.
SAFE_CATEGORY_FIELDS = {"category_id", "category_name", "slot"}

TYPE_ACCESSORY = AvatarSlot.ACCESSORY
TYPE_HAT = AvatarSlot.HAT


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


def cleanup(created_category_ids: list, created_item_ids: list) -> None:
    with SessionLocal() as db:
        if created_item_ids:
            for item_id in created_item_ids:
                item = db.get(ClothingItem, item_id)
                if item is not None:
                    db.delete(item)
        if created_category_ids:
            for category_id in created_category_ids:
                category = db.get(ClothingCategory, category_id)
                if category is not None:
                    db.delete(category)
        user = db.execute(select(User).where(User.username == USERNAME)).scalar_one_or_none()
        if user is not None:
            db.delete(user)
        db.commit()
    print(
        f"\nCleaned up {len(created_item_ids)} items, "
        f"{len(created_category_ids)} categories and 1 test user."
    )


results = []
created_category_ids: list[int] = []
created_item_ids: list[uuid.UUID] = []
item_uuids_by_name: dict[str, uuid.UUID] = {}
first_collection_id: uuid.UUID | None = None


def add_category(name: str, slot: AvatarSlot) -> int:
    with SessionLocal() as db:
        category = ClothingCategory(category_name=name, slot=slot)
        db.add(category)
        db.commit()
        db.refresh(category)
        created_category_ids.append(category.category_id)
        return category.category_id


def add_item(
    category_id: int,
    name: str,
    price: int,
    availability: ClothingAvailability,
    collection_id: uuid.UUID | None = None,
) -> uuid.UUID:
    # One commit per item -> each row gets a distinct created_at (now() is
    # the transaction start time), so ordering is observable and stable.
    with SessionLocal() as db:
        item = ClothingItem(
            name=name,
            description=f"Test item {name}",
            category_id=category_id,
            price=price,
            image_url=f"https://example.com/{name}.png",
            availability_status=availability,
            collection_id=collection_id,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        created_item_ids.append(item.item_id)
        item_uuids_by_name[name] = item.item_id
        return item.item_id


try:
    # --- Setup: an authenticated user --------------------------------------
    client.post("/auth/register", json={"username": USERNAME, "email": f"{USERNAME}@example.com", "password": PASSWORD})
    login = client.post("/auth/login", json={"username": USERNAME, "password": PASSWORD})
    TOKEN = login.json().get("access_token", "")
    AUTH = {"Authorization": f"Bearer {TOKEN}"}
    results.append(("login works", bool(TOKEN), ""))

    # --- Test categories --------------------------------------------------
    # cat_main: 3 AVAILABLE + 1 UNAVAILABLE + 1 UPCOMING.
    # cat_empty: only 1 UNAVAILABLE item -> no AVAILABLE results at all.
    cat_main = add_category(f"CT_{RUN_ID}", TYPE_ACCESSORY)
    cat_empty = add_category(f"CTE_{RUN_ID}", TYPE_HAT)

    AVAILABLE_NAMES = [f"avail_{RUN_ID}_{i}" for i in range(1, 4)]
    first_collection_id = uuid.uuid4()
    for i, name in enumerate(AVAILABLE_NAMES, start=1):
        collection_id = first_collection_id if i == 1 else None
        add_item(cat_main, name, price=100 * i, availability=ClothingAvailability.AVAILABLE, collection_id=collection_id)
    ua_name = f"unavail_{RUN_ID}"
    up_name = f"upcoming_{RUN_ID}"
    add_item(cat_main, ua_name, 999, ClothingAvailability.UNAVAILABLE)
    add_item(cat_main, up_name, 888, ClothingAvailability.UPCOMING)
    add_item(cat_empty, f"emptycat_{RUN_ID}", 500, ClothingAvailability.UNAVAILABLE)

    # --- 1. Authentication ---------------------------------------------------
    results.append(("browse without token -> 401", client.get("/clothing").status_code == 401, ""))
    bad_token = {"Authorization": "Bearer not.a.jwt"}
    results.append(("browse with invalid token -> 401", client.get("/clothing", headers=bad_token).status_code == 401, ""))
    expired_token = {"Authorization": f"Bearer {make_token(str(uuid.uuid4()), timedelta(minutes=-5))}"}
    results.append(("browse with expired token -> 401", client.get("/clothing", headers=expired_token).status_code == 401, ""))
    results.append(("browse with valid token -> 200", client.get("/clothing", headers=AUTH).status_code == 200, ""))

    # --- 2. Filter by category: only that category's AVAILABLE items ---------
    response = client.get("/clothing", headers=AUTH, params={"category_id": cat_main, "limit": 100})
    body = response.json()
    names = [i["name"] for i in body["items"]]
    results.append(
        ("category filter returns only that category's available items",
         response.status_code == 200 and body["total"] == 3 and sorted(names) == sorted(AVAILABLE_NAMES),
         f"total={body['total']} names={names}"),
    )

    category_ids_in_page = {i["category"]["category_id"] for i in body["items"]}
    results.append(("every item belongs to the filtered category", category_ids_in_page == {cat_main}, str(category_ids_in_page)))

    # --- 3. Category with no AVAILABLE items -> empty page -------------------
    response = client.get("/clothing", headers=AUTH, params={"category_id": cat_empty})
    body = response.json()
    results.append(
        ("category with no available items -> 200 empty",
         response.status_code == 200 and body["total"] == 0 and body["items"] == [],
         f"total={body['total']}"),
    )

    # --- 4. Availability rule: UNAVAILABLE/UPCOMING never appear --------------
    all_names = set()
    offset = 0
    while True:
        page = client.get("/clothing", headers=AUTH, params={"category_id": cat_main, "limit": 100, "offset": offset}).json()
        all_names.update(i["name"] for i in page["items"])
        offset += len(page["items"])
        if offset >= page["total"]:
            break
    results.append(
        ("unavailable/upcoming items excluded from browse",
         ua_name not in all_names and up_name not in all_names and set(AVAILABLE_NAMES).issubset(all_names),
         ""),
    )

    # --- 5. Deterministic ordering -------------------------------------------
    r1 = client.get("/clothing", headers=AUTH, params={"category_id": cat_main, "limit": 100})
    r2 = client.get("/clothing", headers=AUTH, params={"category_id": cat_main, "limit": 100})
    ids1 = [i["item_id"] for i in r1.json()["items"]]
    ids2 = [i["item_id"] for i in r2.json()["items"]]
    results.append(("repeated requests return identical order", ids1 == ids2, ""))

    with SessionLocal() as db:
        db_ordered = db.execute(
            select(ClothingItem.item_id)
            .where(ClothingItem.category_id == cat_main, ClothingItem.availability_status == ClothingAvailability.AVAILABLE)
            .order_by(ClothingItem.created_at.desc(), ClothingItem.item_id.desc())
        ).scalars().all()
    results.append(
        ("API order matches DB (created_at DESC, item_id DESC)",
         [uuid.UUID(i) for i in ids1] == list(db_ordered),
         ""),
    )

    # --- 6. Pagination --------------------------------------------------------
    response = client.get("/clothing", headers=AUTH, params={"category_id": cat_main, "limit": 2})
    body = response.json()
    results.append(
        ("limit=2 -> 2 items, total=3",
         len(body["items"]) == 2 and body["total"] == 3,
         f"items={len(body['items'])} total={body['total']}"),
    )

    pages = []
    got = []
    for offset in range(0, 4, 2):
        page = client.get("/clothing", headers=AUTH, params={"category_id": cat_main, "limit": 2, "offset": offset}).json()
        pages.append(page)
        got.extend(i["item_id"] for i in page["items"])
    p1_ids = {i["item_id"] for i in pages[0]["items"]}
    p2_ids = {i["item_id"] for i in pages[1]["items"]}
    results.append(
        ("offset=2 continues without overlap",
         len(pages[0]["items"]) == 2 and len(pages[1]["items"]) == 1 and not (p1_ids & p2_ids),
         f"page0={len(pages[0]['items'])} page1={len(pages[1]['items'])}"),
    )
    results.append(
        ("pages cover the available set exactly",
         set(got) == {str(i) for i in db_ordered},
         ""),
    )
    past_end = client.get("/clothing", headers=AUTH, params={"category_id": cat_main, "limit": 2, "offset": 100}).json()
    results.append(
        ("offset past the end -> empty items",
         past_end["items"] == [] and past_end["total"] == 3,
         ""),
    )

    # --- 7. Category validation ------------------------------------------------
    results.append(("nonexistent in-range category -> 404",
                    client.get("/clothing", headers=AUTH, params={"category_id": 32000}).status_code == 404, ""))
    for bad in [{"category_id": "abc"}, {"category_id": 999999}, {"category_id": 0}, {"category_id": -5}]:
        r = client.get("/clothing", headers=AUTH, params=bad)
        results.append((f"invalid category {bad} -> 422", r.status_code == 422, str(r.status_code)))

    # --- 8. Pagination validation ------------------------------------------------
    for bad in [{"limit": 0}, {"limit": -1}, {"limit": 101}, {"limit": "abc"}, {"offset": -1}, {"offset": "abc"}]:
        r = client.get("/clothing", headers=AUTH, params=bad)
        results.append((f"invalid pagination {bad} -> 422", r.status_code == 422, str(r.status_code)))

    # --- 9. Data safety -----------------------------------------------------------
    response = client.get("/clothing", headers=AUTH, params={"limit": 100})
    body = response.json()
    offenders = {k for item in body["items"] for k in item.keys()} - SAFE_FIELDS
    results.append(("list exposes only safe item fields", not offenders, str(sorted(offenders)) or "clean"))

    category_offenders = {k for item in body["items"] for k in item["category"].keys()} - SAFE_CATEGORY_FIELDS
    results.append(("category object exposes only safe fields", not category_offenders, str(sorted(category_offenders)) or "clean"))

    results.append(
        ("every item has availability_status 'available'",
         all(i["availability_status"] == "available" for i in body["items"]),
         ""),
    )

    # collection_id round-trips when set, null otherwise.
    with_collection = next(i for i in body["items"] if i["name"] == AVAILABLE_NAMES[0])
    results.append(
        ("collection_id surfaces when set",
         with_collection["collection_id"] == str(first_collection_id),
         str(first_collection_id)),
    )
    without_collection = next(i for i in body["items"] if i["name"] == AVAILABLE_NAMES[1])
    results.append(("collection_id is null otherwise", without_collection["collection_id"] is None, ""))

    # --- 10. Category metadata exposed ------------------------------------
    response = client.get("/clothing", headers=AUTH, params={"category_id": cat_main, "limit": 100})
    first = response.json()["items"][0]
    results.append(
        ("category metadata (id, name, slot) exposed",
         first["category"] == {"category_id": cat_main, "category_name": f"CT_{RUN_ID}", "slot": "accessory"},
         str(first["category"])),
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