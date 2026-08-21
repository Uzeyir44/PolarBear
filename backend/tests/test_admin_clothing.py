"""
End-to-end checks of the admin clothing-management module (/admin/clothing).

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_admin_clothing

Covers:
  - Authorization: unauthenticated -> 401 and normal users -> 403 on every
    /admin/clothing endpoint; an administrator is granted access.
  - Categories: GET /admin/clothing/categories returns the seeded
    clothing_categories lookup rows (id/name/slot) for the admin form.
  - Create: 201 with database-generated item_id/created_at, availability
    defaults to AVAILABLE, category must exist (404 otherwise), price >= 0,
    enum-only availability, whitespace trimming, client-supplied
    id/timestamp ignored.
  - Listing: newest-first deterministic ordering, pagination tiling, name
    AND description search (case-insensitive, ILIKE-escaped), category and
    availability filters, exact field whitelist. Unlike public browse, the
    admin list includes UNAVAILABLE/UPCOMING items.
  - Detail: full administrative view + 404 for a nonexistent item.
  - Update: every catalog field independently, category re-validation,
    clearing nullable fields with null, immutability of item_id/created_at,
    404/422 error cases.
  - Public compatibility: an AVAILABLE item created by the admin appears in
    GET /clothing and is purchasable; marking it UNAVAILABLE hides it from
    browse and makes purchase return 409.
  - Delete safety: an unreferenced item is hard-deleted (204); an item
    referenced by a user_wardrobe row returns 409 and BOTH the item and the
    ownership record survive (RESTRICT FKs + audit history); nonexistent -> 404.

All rows created by the test (users, categories, items) are deleted
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
ADMIN_USERNAME = f"cadmin_{RUN_ID}"
ADMIN_EMAIL = f"cadmin_{RUN_ID}@example.com"
USERNAME = f"cuser_{RUN_ID}"
PASSWORD = "SuperSecret123!"

# Every field the admin API may expose for a clothing item.
SAFE_FIELDS = {
    "item_id",
    "name",
    "description",
    "category_id",
    "category_name",
    "slot",
    "price",
    "image_url",
    "availability_status",
    "collection_id",
    "created_at",
}
# Input schemas must never accept these — they are database-owned.
IMMUTABLE_FIELDS = {"item_id", "created_at"}

REFERENCED_ERROR = (
    "Clothing item cannot be deleted because users own or wear it. "
    "Mark it UNAVAILABLE instead."
)
CATEGORY_NOT_FOUND = "Category not found"

# The seeded lookup rows from the initial migration (name -> slot).
SEEDED_CATEGORIES = {
    "Hairstyles": "hair",
    "Hats": "hat",
    "Tops": "top",
    "Bottoms": "bottom",
    "Sneakers": "shoes",
    "Sunglasses": "accessory",
}


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


def cleanup(created_category_ids: list[int], created_item_ids: list[uuid.UUID]) -> None:
    # coin_transactions.user_id is RESTRICT -> ledger rows go first; then
    # deleting the users cascades their user_wardrobe rows, which releases
    # the RESTRICT FKs so the items can be removed.
    with SessionLocal() as db:
        for username in [ADMIN_USERNAME, USERNAME]:
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is not None:
                for tx in db.execute(
                    select(CoinTransaction).where(CoinTransaction.user_id == user.user_id)
                ).scalars().all():
                    db.delete(tx)
                db.commit()
                db.delete(user)
                db.commit()
    with SessionLocal() as db:
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
        f"\nCleaned up 2 test users, {len(created_item_ids)} items and "
        f"{len(created_category_ids)} categories."
    )


results = []
created_category_ids: list[int] = []
created_item_ids: list[uuid.UUID] = []


def add_category(name: str, slot: AvatarSlot) -> int:
    with SessionLocal() as db:
        category = ClothingCategory(category_name=name, slot=slot)
        db.add(category)
        db.commit()
        db.refresh(category)
        created_category_ids.append(category.category_id)
        return category.category_id


try:
    # --- Setup: an admin (promoted via SQL) + one normal user ------------------
    client.post(
        "/auth/register",
        json={"username": ADMIN_USERNAME, "email": ADMIN_EMAIL, "password": PASSWORD},
    )
    with SessionLocal() as db:
        admin_id = db.execute(select(User.user_id).where(User.username == ADMIN_USERNAME)).scalar_one()
        db.get(User, admin_id).is_admin = True
        db.commit()

    client.post(
        "/auth/register",
        json={"username": USERNAME, "email": f"{USERNAME}@example.com", "password": PASSWORD},
    )

    login = client.post("/auth/login", json={"username": ADMIN_USERNAME, "password": PASSWORD})
    ADMIN_TOKEN = login.json().get("access_token", "")
    ADMIN_AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    results.append(("admin login works", bool(ADMIN_TOKEN), ""))

    login = client.post("/auth/login", json={"username": USERNAME, "password": PASSWORD})
    USER_TOKEN = login.json().get("access_token", "")
    USER_AUTH = {"Authorization": f"Bearer {USER_TOKEN}"}
    results.append(("non-admin login works", bool(USER_TOKEN), ""))

    # --- Test categories --------------------------------------------------------
    cat_a = add_category(f"CTA_{RUN_ID}", AvatarSlot.ACCESSORY)
    cat_b = add_category(f"CTB_{RUN_ID}", AvatarSlot.HAT)

    # --- 1. Authorization: 401 / 403 / granted --------------------------------
    valid_body = {
        "name": f"AuthDummy {RUN_ID}",
        "category_id": cat_a,
        "price": 10,
        "image_url": "https://example.com/auth-dummy.png",
    }
    bogus = str(uuid.uuid4())
    for method, path, kwargs in [
        ("GET", "/admin/clothing", {}),
        ("GET", "/admin/clothing/categories", {}),
        ("POST", "/admin/clothing", {"json": valid_body}),
        ("GET", f"/admin/clothing/{bogus}", {}),
        ("PATCH", f"/admin/clothing/{bogus}", {"json": {"name": "x"}}),
        ("DELETE", f"/admin/clothing/{bogus}", {}),
    ]:
        unauth = getattr(client, method.lower())(path, **kwargs)
        user = getattr(client, method.lower())(path, headers=USER_AUTH, **kwargs)
        admin = getattr(client, method.lower())(path, headers=ADMIN_AUTH, **kwargs)
        results.append((f"unauth {method} {path} -> 401", unauth.status_code == 401, str(unauth.status_code)))
        results.append((f"normal user {method} {path} -> 403", user.status_code == 403, str(user.status_code)))
        results.append((f"admin {method} {path} -> not auth error", admin.status_code not in (401, 403), str(admin.status_code)))
        if method == "POST" and admin.status_code == 201:
            created_item_ids.append(uuid.UUID(admin.json()["item_id"]))

    bad_token = {"Authorization": "Bearer not.a.jwt"}
    results.append(("invalid token -> 401", client.get("/admin/clothing", headers=bad_token).status_code == 401, ""))
    expired_token = {"Authorization": f"Bearer {make_token(admin_id, timedelta(minutes=-5))}"}
    results.append(("expired token -> 401", client.get("/admin/clothing", headers=expired_token).status_code == 401, ""))

    # --- 2. Categories endpoint -------------------------------------------------
    response = client.get("/admin/clothing/categories", headers=ADMIN_AUTH)
    body = response.json()
    by_name = {c["category_name"]: c for c in body}
    seeded_ok = all(
        name in by_name and by_name[name]["slot"] == slot
        for name, slot in SEEDED_CATEGORIES.items()
    )
    results.append(
        ("categories endpoint returns all seeded rows with slots",
         response.status_code == 200 and seeded_ok, str(sorted(by_name))),
    )
    offenders = {k for c in body for k in c.keys()} - {"category_id", "category_name", "slot"}
    results.append(("categories expose only id/name/slot", not offenders, str(sorted(offenders)) or "clean"))
    ids_in_order = [c["category_id"] for c in body]
    results.append(("categories ordered by category_id", ids_in_order == sorted(ids_in_order), str(ids_in_order[:6])))

    # --- 3. Create ----------------------------------------------------------------
    response = client.post(
        "/admin/clothing",
        headers=ADMIN_AUTH,
        json={
            "name": f"Admin Tee {RUN_ID}",
            "description": f"Created by admin test {RUN_ID}",
            "category_id": cat_a,
            "price": 250,
            "image_url": f"https://example.com/{RUN_ID}-tee.png",
        },
    )
    body = response.json()
    ok = (
        response.status_code == 201
        and body["name"] == f"Admin Tee {RUN_ID}"
        and body["category_id"] == cat_a
        and body["category_name"] == f"CTA_{RUN_ID}"
        and body["slot"] == "accessory"
        and body["price"] == 250
        and body["availability_status"] == "available"
        and len(str(body["item_id"])) == 36
        and body["collection_id"] is None
    )
    results.append(("create valid item -> 201, defaults to available", ok, str(body.get("item_id"))))
    ITEM_MAIN = uuid.UUID(body["item_id"])
    created_item_ids.append(ITEM_MAIN)
    main_created_at = body["created_at"]
    results.append(("create returns server timestamp", isinstance(datetime.fromisoformat(main_created_at.replace("Z", "+00:00")) if main_created_at.endswith("Z") else datetime.fromisoformat(main_created_at), datetime), str(main_created_at)))

    # Client cannot smuggle item_id/created_at on create.
    smuggled_id = str(uuid.uuid4())
    response = client.post(
        "/admin/clothing",
        headers=ADMIN_AUTH,
        json={
            "name": f"Smuggled {RUN_ID}",
            "category_id": cat_a,
            "price": 1,
            "image_url": "https://example.com/s.png",
            "item_id": smuggled_id,
            "created_at": "2020-01-01T00:00:00",
        },
    )
    body = response.json()
    results.append(
        ("create ignores client-supplied id/timestamp",
         response.status_code == 201 and str(body["item_id"]) != smuggled_id and body["created_at"] != "2020-01-01T00:00:00",
         str(body.get("item_id"))),
    )
    created_item_ids.append(uuid.UUID(body["item_id"]))

    # Unknown category -> 404 (in SMALLINT range but nonexistent).
    response = client.post(
        "/admin/clothing",
        headers=ADMIN_AUTH,
        json={"name": f"NoCat {RUN_ID}", "category_id": 32000, "price": 5, "image_url": "https://example.com/x.png"},
    )
    results.append(("create unknown category -> 404", response.status_code == 404 and response.json()["detail"] == CATEGORY_NOT_FOUND, str(response.json().get("detail"))))

    # Price validation (DB CHECK is price >= 0).
    for bad_price in [-1, -100]:
        r = client.post(
            "/admin/clothing",
            headers=ADMIN_AUTH,
            json={"name": f"NegPrice {RUN_ID}", "category_id": cat_a, "price": bad_price, "image_url": "https://example.com/n.png"},
        )
        results.append((f"create negative price {bad_price} -> 422", r.status_code == 422, str(r.status_code)))
    r = client.post(
        "/admin/clothing",
        headers=ADMIN_AUTH,
        json={"name": f"StrPrice {RUN_ID}", "category_id": cat_a, "price": "abc", "image_url": "https://example.com/n.png"},
    )
    results.append(("create non-integer price -> 422", r.status_code == 422, str(r.status_code)))

    # Name / image validation.
    r = client.post(
        "/admin/clothing",
        headers=ADMIN_AUTH,
        json={"name": "   ", "category_id": cat_a, "price": 5, "image_url": "https://example.com/b.png"},
    )
    results.append(("create blank name -> 422", r.status_code == 422, str(r.status_code)))
    r = client.post(
        "/admin/clothing",
        headers=ADMIN_AUTH,
        json={"name": f"NoImg {RUN_ID}", "category_id": cat_a, "price": 5},
    )
    results.append(("create missing image_url -> 422", r.status_code == 422, str(r.status_code)))
    r = client.post(
        "/admin/clothing",
        headers=ADMIN_AUTH,
        json={"name": f"BlankImg {RUN_ID}", "category_id": cat_a, "price": 5, "image_url": "  "},
    )
    results.append(("create blank image_url -> 422", r.status_code == 422, str(r.status_code)))

    # Availability must be one of the existing enum values.
    r = client.post(
        "/admin/clothing",
        headers=ADMIN_AUTH,
        json={"name": f"BadAvail {RUN_ID}", "category_id": cat_a, "price": 5, "image_url": "https://example.com/ba.png", "availability_status": "discontinued"},
    )
    results.append(("create bogus availability -> 422", r.status_code == 422, str(r.status_code)))

    # Whitespace trimmed; explicit availability accepted; collection round-trips.
    collection_id = str(uuid.uuid4())
    response = client.post(
        "/admin/clothing",
        headers=ADMIN_AUTH,
        json={
            "name": f"  Trimmed {RUN_ID}  ",
            "description": "  Padded description  ",
            "category_id": cat_b,
            "price": 0,
            "image_url": f"  https://example.com/{RUN_ID}-trim.png  ",
            "availability_status": "upcoming",
            "collection_id": collection_id,
        },
    )
    body = response.json()
    ok = (
        response.status_code == 201
        and body["name"] == f"Trimmed {RUN_ID}"
        and body["description"] == "Padded description"
        and body["image_url"] == f"https://example.com/{RUN_ID}-trim.png"
        and body["slot"] == "hat"
        and body["price"] == 0
        and body["availability_status"] == "upcoming"
        and body["collection_id"] == collection_id
    )
    results.append(("create trims whitespace, price 0 ok, upcoming+collection kept", ok, str(body.get("availability_status"))))
    ITEM_TRIMMED = uuid.UUID(body["item_id"])
    created_item_ids.append(ITEM_TRIMMED)

    # --- 4. List --------------------------------------------------------------------
    # q=RUN_ID scopes the listing to this run's items regardless of seed data.
    # Four items carry RUN_ID by now: Admin Tee, Smuggled, Trimmed and the
    # AuthDummy created during the authorization matrix.
    response = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 100})
    body = response.json()
    names = [i["name"] for i in body["items"]]
    expected_names = [f"Admin Tee {RUN_ID}", f"Smuggled {RUN_ID}", f"Trimmed {RUN_ID}", f"AuthDummy {RUN_ID}"]
    results.append(
        ("list q=RUN_ID returns exactly the run's items",
         body["total"] == len(expected_names) and set(names) == set(expected_names),
         f"total={body['total']} names={names}"),
    )

    offenders = {k for item in body["items"] for k in item.keys()} - SAFE_FIELDS
    results.append(("list exposes only safe admin fields", not offenders, str(sorted(offenders)) or "clean"))

    # Newest-first ordering matches a direct DB query.
    with SessionLocal() as db:
        db_ordered = db.execute(
            select(ClothingItem.item_id)
            .where(ClothingItem.name.ilike(f"%{RUN_ID}%"))
            .order_by(ClothingItem.created_at.desc(), ClothingItem.item_id.desc())
        ).scalars().all()
    api_ordered = [uuid.UUID(i["item_id"]) for i in body["items"]]
    results.append(("list is newest-first (matches DB order)", api_ordered == list(db_ordered), str(api_ordered)))

    r1 = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 100})
    r2 = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 100})
    results.append(("repeated requests return identical order", [i["item_id"] for i in r1.json()["items"]] == [i["item_id"] for i in r2.json()["items"]], ""))

    page1 = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 2, "offset": 0}).json()
    page2 = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 2, "offset": 2}).json()
    p1_ids = {i["item_id"] for i in page1["items"]}
    p2_ids = {i["item_id"] for i in page2["items"]}
    results.append(
        ("pagination limit=2 tiles without overlap",
         len(page1["items"]) == 2 and len(page2["items"]) == 2 and not (p1_ids & p2_ids) and page1["total"] == 4,
         f"p1={len(page1['items'])} p2={len(page2['items'])}"),
    )

    # Search: case-insensitive name fragment...
    response = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"q": f"admin tee {RUN_ID}"})
    results.append(("search by name fragment is case-insensitive", response.json()["total"] == 1, str(response.json()["total"])))
    # ...and description fragment.
    response = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"q": f"Created by admin test {RUN_ID}"})
    results.append(("search matches description too", response.json()["total"] == 1, str(response.json()["total"])))
    # Wildcards are escaped.
    response = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"q": "50%_wildcard"})
    results.append(("search escapes ILIKE wildcards", response.json()["total"] == 0, str(response.json()["total"])))
    response = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"q": "no-such-item-xyzzy"})
    results.append(("search with no match -> empty", response.json()["total"] == 0 and response.json()["items"] == [], ""))

    # Category filter.
    response = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"category_id": cat_b})
    items_b = response.json()["items"]
    results.append(
        ("category filter returns only that category's items",
         response.json()["total"] == 1 and items_b[0]["item_id"] == str(ITEM_TRIMMED),
         str(response.json()["total"])),
    )
    response = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"category_id": 32000})
    results.append(("filter unknown category -> 404", response.status_code == 404 and response.json()["detail"] == CATEGORY_NOT_FOUND, str(response.status_code)))

    # Availability filter — including statuses public browse never shows.
    all_run_ids = {str(i) for i in created_item_ids}
    trimmed_id = str(ITEM_TRIMMED)
    for avail, excluded in [("available", {trimmed_id}), ("upcoming", all_run_ids - {trimmed_id})]:
        response = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"q": RUN_ID, "availability": avail, "limit": 100})
        got = {i["item_id"] for i in response.json()["items"]}
        results.append((f"availability filter '{avail}' -> only matching", not (got & set(excluded)) and bool(got), str(got)))
    response = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"availability": "bogus"})
    results.append(("availability filter bogus value -> 422", response.status_code == 422, str(response.status_code)))

    # Admin sees UNAVAILABLE/UPCOMING items (unlike public browse).
    response = client.get("/admin/clothing", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 100})
    got_availabilities = {i["availability_status"] for i in response.json()["items"]}
    results.append(
        ("admin list includes unavailable/upcoming items",
         {"available", "upcoming"}.issubset(got_availabilities),
         str(got_availabilities)),
    )

    # --- 5. Detail ---------------------------------------------------------------------
    response = client.get(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH)
    body = response.json()
    results.append(
        ("detail returns 200 with the full view",
         response.status_code == 200 and set(body.keys()) == SAFE_FIELDS and body["name"] == f"Admin Tee {RUN_ID}",
         str(sorted(body.keys()))),
    )
    results.append(("detail nonexistent item -> 404", client.get(f"/admin/clothing/{uuid.uuid4()}", headers=ADMIN_AUTH).status_code == 404, ""))

    # --- 6. Update ----------------------------------------------------------------------
    response = client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"name": f"Renamed Tee {RUN_ID}"})
    body = response.json()
    results.append(
        ("patch name only -> others untouched",
         response.status_code == 200 and body["name"] == f"Renamed Tee {RUN_ID}"
         and body["price"] == 250 and body["category_id"] == cat_a and body["availability_status"] == "available",
         str(body.get("name"))),
    )

    response = client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"price": 300})
    results.append(("patch price only -> 200", response.status_code == 200 and response.json()["price"] == 300, str(response.json().get("price"))))

    response = client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"description": None})
    results.append(("patch null clears description", response.status_code == 200 and response.json()["description"] is None, str(response.json().get("description"))))

    new_collection = str(uuid.uuid4())
    response = client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"collection_id": new_collection})
    results.append(("patch sets collection_id", response.status_code == 200 and response.json()["collection_id"] == new_collection, ""))
    response = client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"collection_id": None})
    results.append(("patch null clears collection_id", response.status_code == 200 and response.json()["collection_id"] is None, ""))

    # Category change revalidates and inherits the new slot.
    response = client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"category_id": cat_b})
    body = response.json()
    results.append(
        ("patch category moves item and inherits slot",
         response.status_code == 200 and body["category_id"] == cat_b and body["category_name"] == f"CTB_{RUN_ID}" and body["slot"] == "hat",
         str(body.get("slot"))),
    )
    response = client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"category_id": 32000})
    results.append(("patch unknown category -> 404", response.status_code == 404 and response.json()["detail"] == CATEGORY_NOT_FOUND, str(response.status_code)))

    response = client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"price": -7})
    results.append(("patch negative price -> 422", response.status_code == 422, str(response.status_code)))
    response = client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"availability_status": "sold_out_forever"})
    results.append(("patch bogus availability -> 422", response.status_code == 422, str(response.status_code)))
    response = client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"name": ""})
    results.append(("patch empty name -> 422", response.status_code == 422, str(response.status_code)))

    # item_id/created_at are immutable; client values are ignored.
    before = client.get(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH).json()
    response = client.patch(
        f"/admin/clothing/{ITEM_MAIN}",
        headers=ADMIN_AUTH,
        json={"name": before["name"], "item_id": str(uuid.uuid4()), "created_at": "2020-01-01T00:00:00"},
    )
    after = client.get(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH).json()
    results.append(
        ("patch cannot change id/created_at",
         response.status_code == 200 and after["item_id"] == before["item_id"] and after["created_at"] == before["created_at"],
         ""),
    )

    results.append(("patch nonexistent item -> 404", client.patch(f"/admin/clothing/{uuid.uuid4()}", headers=ADMIN_AUTH, json={"name": "x"}).status_code == 404, ""))
    results.append(("normal user patch -> 403", client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=USER_AUTH, json={"name": "x"}).status_code == 403, ""))

    # --- 7. Public compatibility ----------------------------------------------------------
    # Restore the item to AVAILABLE in cat_a at a low price for the purchase check.
    client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"category_id": cat_a, "price": 50, "availability_status": "available"})

    response = client.get("/clothing", headers=USER_AUTH, params={"category_id": cat_a, "limit": 100})
    browsed_ids = [i["item_id"] for i in response.json()["items"]]
    results.append(("admin-created AVAILABLE item appears in public browse", str(ITEM_MAIN) in browsed_ids, str(browsed_ids)))

    # Mark UNAVAILABLE -> hidden from browse and unpurchasable.
    client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"availability_status": "unavailable"})
    response = client.get("/clothing", headers=USER_AUTH, params={"category_id": cat_a, "limit": 100})
    browsed_ids = [i["item_id"] for i in response.json()["items"]]
    results.append(("UNAVAILABLE item disappears from public browse", str(ITEM_MAIN) not in browsed_ids, str(browsed_ids)))
    response = client.post(f"/clothing/{ITEM_MAIN}/purchase", headers=USER_AUTH)
    results.append(("purchase UNAVAILABLE item -> 409", response.status_code == 409, str(response.status_code)))

    # Back to AVAILABLE -> purchasable (give the buyer coins first).
    with SessionLocal() as db:
        user_row = db.execute(select(User).where(User.username == USERNAME)).scalar_one()
        user_row.coin_balance = 1000
        db.commit()
    client.patch(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH, json={"availability_status": "available"})
    response = client.post(f"/clothing/{ITEM_MAIN}/purchase", headers=USER_AUTH)
    body = response.json()
    results.append(
        ("purchase AVAILABLE admin-created item -> 200",
         response.status_code == 200 and body.get("amount_spent") == 50 and bool(body.get("wardrobe_id")),
         str(response.status_code)),
    )
    with SessionLocal() as db:
        owned = db.execute(
            select(UserWardrobe.wardrobe_id).where(
                UserWardrobe.user_id == db.execute(select(User.user_id).where(User.username == USERNAME)).scalar_one(),
                UserWardrobe.item_id == ITEM_MAIN,
            )
        ).scalar_one_or_none()
    results.append(("purchase created the wardrobe ownership row", owned is not None, ""))

    # --- 8. Delete safety -------------------------------------------------------------------
    # An item referenced by a wardrobe row must NOT be destroyed.
    response = client.delete(f"/admin/clothing/{ITEM_MAIN}", headers=ADMIN_AUTH)
    results.append(("delete wardrobe-referenced item -> 409", response.status_code == 409 and response.json()["detail"] == REFERENCED_ERROR, str(response.json().get("detail"))))
    with SessionLocal() as db:
        still_there = db.get(ClothingItem, ITEM_MAIN) is not None
        wardrobe_intact = db.execute(select(UserWardrobe.wardrobe_id).where(UserWardrobe.item_id == ITEM_MAIN)).scalar_one_or_none() is not None
    results.append(("referenced item AND ownership row survive the 409", still_there and wardrobe_intact, ""))

    # Unreferenced item deletes cleanly.
    response = client.delete(f"/admin/clothing/{ITEM_TRIMMED}", headers=ADMIN_AUTH)
    results.append(("delete unreferenced item -> 204", response.status_code == 204, str(response.status_code)))
    results.append(("deleted item is gone -> 404", client.get(f"/admin/clothing/{ITEM_TRIMMED}", headers=ADMIN_AUTH).status_code == 404, ""))
    created_item_ids.remove(ITEM_TRIMMED)

    results.append(("delete nonexistent -> 404", client.delete(f"/admin/clothing/{uuid.uuid4()}", headers=ADMIN_AUTH).status_code == 404, ""))

finally:
    cleanup(created_category_ids, created_item_ids)

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)
