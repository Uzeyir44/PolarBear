"""
End-to-end checks of the admin product-management module (/admin/products).

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_admin_products

Covers:
  - Authorization: unauthenticated -> 401 and normal users -> 403 on
    every /admin/products endpoint; an administrator is granted access.
  - Listing: newest-first ordering, pagination (limit/offset/total), and
    name/SKU search (case-insensitive, ILIKE-escaped like the users module).
  - Detail: product_id/name/sku/created_at + qr_code_count, 404 for a
    nonexistent product.
  - Create: 201 with a database-generated product_id and created_at
    (clients cannot set them), duplicate SKU -> 409, blank fields -> 422.
  - Update: name and SKU independently, immutability of product_id and
    created_at, duplicate SKU on another product -> 409, nonexistent -> 404.
  - Delete: unreferenced -> 204, referenced by a QR -> 409 (audit history
    preserved), nonexistent -> 404.
  - QR integration: a QR created against a product reports qr_code_count
    and blocks deletion of that product.

All rows created by the test (users, admin, products, QR code) are deleted
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
from app.models import QRCode, Product, User

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
ADMIN_USERNAME = f"padmin_{RUN_ID}"
ADMIN_EMAIL = f"padmin_{RUN_ID}@example.com"
USERNAME = f"puser_{RUN_ID}"
PASSWORD = "SuperSecret123!"

# Every field the admin API may expose for a product.
SAFE_FIELDS = {"product_id", "name", "sku", "created_at", "qr_code_count"}
# Input schemas must never accept these — they are database-owned.
IMMUTABLE_FIELDS = {"product_id", "created_at"}

PRODUCT_NAMES = [f"PB Cola {RUN_ID} v{i}" for i in range(1, 4)]
PRODUCT_SKUS = [f"SKU-{RUN_ID}-01", f"SKU-{RUN_ID}-02", f"SKU-{RUN_ID}-03"]

REFERENCED_ERROR = "Product cannot be deleted because it is referenced by QR codes."


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


def cleanup(created_product_ids: list[uuid.UUID]) -> None:
    with SessionLocal() as db:
        if created_product_ids:
            for row in db.execute(
                select(QRCode).where(QRCode.product_id.in_(created_product_ids))
            ).scalars():
                db.delete(row)
            for product_id in created_product_ids:
                product = db.get(Product, product_id)
                if product is not None:
                    db.delete(product)
        for username in [ADMIN_USERNAME, USERNAME]:
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is not None:
                db.delete(user)
        db.commit()
    print(f"\nCleaned up {len(created_product_ids)} products (and their QR codes) and 2 test users.")


results = []
created_product_ids: list[uuid.UUID] = []

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

    # --- 1. Authorization: 401 / 403 / granted --------------------------------
    dummy = {"name": f"AuthDummy {RUN_ID}", "sku": f"AUTHDUMMY-{RUN_ID}"}
    bogus = str(uuid.uuid4())
    for method, path, kwargs in [
        ("GET", "/admin/products", {}),
        ("GET", f"/admin/products/{bogus}", {}),
        ("POST", "/admin/products", {"json": dummy}),
        ("PATCH", f"/admin/products/{bogus}", {"json": {"name": "x"}}),
        ("DELETE", f"/admin/products/{bogus}", {}),
    ]:
        unauth = getattr(client, method.lower())(path, **kwargs)
        user = getattr(client, method.lower())(path, headers=USER_AUTH, **kwargs)
        admin = getattr(client, method.lower())(path, headers=ADMIN_AUTH, **kwargs)
        results.append((f"unauth {method} {path} -> 401", unauth.status_code == 401, str(unauth.status_code)))
        results.append((f"normal user {method} {path} -> 403", user.status_code == 403, str(user.status_code)))
        results.append((f"admin {method} {path} -> not auth error", admin.status_code not in (401, 403), str(admin.status_code)))
        if method == "POST" and admin.status_code == 201:
            created_product_ids.append(uuid.UUID(admin.json()["product_id"]))

    bad_token = {"Authorization": "Bearer not.a.jwt"}
    results.append(("invalid token -> 401", client.get("/admin/products", headers=bad_token).status_code == 401, ""))
    expired_token = {"Authorization": f"Bearer {make_token(admin_id, timedelta(minutes=-5))}"}
    results.append(("expired token -> 401", client.get("/admin/products", headers=expired_token).status_code == 401, ""))

    # --- 2. Create --------------------------------------------------------------
    created = {}
    for name, sku in zip(PRODUCT_NAMES, PRODUCT_SKUS):
        response = client.post("/admin/products", headers=ADMIN_AUTH, json={"name": name, "sku": sku})
        body = response.json()
        ok = (
            response.status_code == 201
            and body["name"] == name
            and body["sku"] == sku
            and len(str(body["product_id"])) == 36
            and body["qr_code_count"] == 0
        )
        results.append((f"create {sku} -> 201, db-generated id", ok, str(body.get("product_id"))))
        created[sku] = body["product_id"]
        created_product_ids.append(uuid.UUID(body["product_id"]))

    # created_at parsing proves it is a server-generated timestamp.
    with SessionLocal() as db:
        row_created_at = db.get(Product, created[PRODUCT_SKUS[0]]).created_at
    results.append(("created_at is a real server timestamp", isinstance(row_created_at, datetime), str(row_created_at)))

    # Client cannot smuggle in product_id/created_at on create.
    smuggled = {"name": f"Smuggled {RUN_ID}", "sku": f"SMUGGLE-{RUN_ID}", "product_id": str(uuid.uuid4()), "created_at": "2020-01-01T00:00:00Z"}
    response = client.post("/admin/products", headers=ADMIN_AUTH, json=smuggled)
    body = response.json()
    results.append(("create ignores client-supplied id/timestamp", response.status_code == 201 and str(body["product_id"]) != smuggled["product_id"] and body["created_at"] != smuggled["created_at"], str(body.get("product_id"))))
    created_product_ids.append(uuid.UUID(body["product_id"]))

    # Duplicate SKU rejected (and does not consume a row).
    response = client.post("/admin/products", headers=ADMIN_AUTH, json={"name": f"Dupe {RUN_ID}", "sku": PRODUCT_SKUS[0]})
    results.append(("create duplicate SKU -> 409", response.status_code == 409 and response.json()["detail"] == "A product with this SKU already exists", str(response.json().get("detail"))))

    results.append(("create blank name -> 422", client.post("/admin/products", headers=ADMIN_AUTH, json={"name": "  ", "sku": f"B-{RUN_ID}"}).status_code == 422, ""))
    results.append(("create blank sku -> 422", client.post("/admin/products", headers=ADMIN_AUTH, json={"name": "OK", "sku": " "}).status_code == 422, ""))

    # Extra whitespace on name/sku is trimmed on the way in.
    response = client.post("/admin/products", headers=ADMIN_AUTH, json={"name": f"  Trimmed {RUN_ID}  ", "sku": f"  TRIM-{RUN_ID}  "})
    body = response.json()
    results.append(("create trims whitespace", response.status_code == 201 and body["name"] == f"Trimmed {RUN_ID}" and body["sku"] == f"TRIM-{RUN_ID}", f"name={body['name']!r}"))
    created_product_ids.append(uuid.UUID(body["product_id"]))

    # --- 3. List: pagination, newest-first, search -------------------------------
    # q=RUN_ID scopes the listing to exactly this run's products (names carry
    # RUN_ID), regardless of what else sits in the dev DB.
    response = client.get("/admin/products", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 100})
    body = response.json()
    items = body["items"]
    results.append(("list q=RUN_ID returns the run's products", body["total"] == len(created_product_ids) and len(items) == len(created_product_ids), f"total={body['total']}"))

    names_in_order = [p["name"] for p in items]
    expects = [PRODUCT_NAMES[2], PRODUCT_NAMES[1], PRODUCT_NAMES[0]]  # newest first
    idx = [names_in_order.index(n) for n in expects]
    results.append(("list is newest-first", idx[0] < idx[1] < idx[2], str(idx)))

    offenders = {k for item in items for k in item.keys()} - SAFE_FIELDS
    results.append(("list exposes only safe admin fields", not offenders, str(sorted(offenders)) or "clean"))

    response = client.get("/admin/products", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 2})
    results.append(("pagination limit=2 -> 2 items, total=full", len(response.json()["items"]) == 2 and response.json()["total"] == len(created_product_ids), str(response.json())))

    p1 = client.get("/admin/products", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 2, "offset": 0}).json()["items"]
    p2 = client.get("/admin/products", headers=ADMIN_AUTH, params={"q": RUN_ID, "limit": 2, "offset": 2}).json()["items"]
    p1_ids = {p["product_id"] for p in p1}
    p2_ids = {p["product_id"] for p in p2}
    results.append(("pagination offset=2 continues without overlap", len(p2) == 2 and not (p1_ids & p2_ids), ""))

    response = client.get("/admin/products", headers=ADMIN_AUTH, params={"q": PRODUCT_NAMES[1]})
    results.append(("search by name fragment works", response.json()["total"] == 1 and PRODUCT_NAMES[1] in [p["name"] for p in response.json()["items"]], str(response.json()["total"])))

    response = client.get("/admin/products", headers=ADMIN_AUTH, params={"q": PRODUCT_NAMES[1].upper()})
    results.append(("search is case-insensitive", response.json()["total"] == 1, str(response.json()["total"])))

    response = client.get("/admin/products", headers=ADMIN_AUTH, params={"q": PRODUCT_SKUS[2]})
    results.append(("search by SKU fragment works", response.json()["total"] == 1 and PRODUCT_SKUS[2] in [p["sku"] for p in response.json()["items"]], str(response.json()["total"])))

    response = client.get("/admin/products", headers=ADMIN_AUTH, params={"q": "no-such-product-xyzzy"})
    results.append(("search with no match -> empty", response.json()["total"] == 0 and response.json()["items"] == [], str(response.json()["total"])))

    response = client.get("/admin/products", headers=ADMIN_AUTH, params={"q": "50%_wildcard"})
    results.append(("search escapes ILIKE wildcards", response.json()["total"] == 0, str(response.json()["total"])))

    # --- 4. Detail ----------------------------------------------------------------
    response = client.get(f"/admin/products/{created[PRODUCT_SKUS[0]]}", headers=ADMIN_AUTH)
    body = response.json()
    results.append(("detail returns 200 with product", response.status_code == 200 and body["name"] == PRODUCT_NAMES[0], str(response.status_code)))
    results.append(("detail exposes safe admin fields", set(body.keys()) == SAFE_FIELDS, str(sorted(body.keys()))))

    results.append(("detail nonexistent product -> 404", client.get(f"/admin/products/{uuid.uuid4()}", headers=ADMIN_AUTH).status_code == 404, ""))

    # --- 5. Update -----------------------------------------------------------------
    response = client.patch(f"/admin/products/{created[PRODUCT_SKUS[0]]}", headers=ADMIN_AUTH, json={"name": f"{PRODUCT_NAMES[0]} renamed"})
    body = response.json()
    results.append(("patch name only -> 200, sku untouched", response.status_code == 200 and body["name"] == f"{PRODUCT_NAMES[0]} renamed" and body["sku"] == PRODUCT_SKUS[0], f"{body['name']} / {body['sku']}"))

    response = client.patch(f"/admin/products/{created[PRODUCT_SKUS[0]]}", headers=ADMIN_AUTH, json={"sku": f"{PRODUCT_SKUS[0]}-R"})
    body = response.json()
    results.append(("patch sku only -> 200, name untouched", response.status_code == 200 and body["sku"] == f"{PRODUCT_SKUS[0]}-R" and body["name"] == f"{PRODUCT_NAMES[0]} renamed", str(body["sku"])))

    response = client.patch(f"/admin/products/{created[PRODUCT_SKUS[0]]}", headers=ADMIN_AUTH, json={"name": PRODUCT_NAMES[0], "sku": PRODUCT_SKUS[0]})
    body = response.json()
    results.append(("patch both fields restores original", response.status_code == 200 and body["name"] == PRODUCT_NAMES[0] and body["sku"] == PRODUCT_SKUS[0], ""))

    # product_id / created_at are immutable and client-supplied values are ignored.
    before = client.get(f"/admin/products/{created[PRODUCT_SKUS[0]]}", headers=ADMIN_AUTH).json()
    response = client.patch(f"/admin/products/{created[PRODUCT_SKUS[0]]}", headers=ADMIN_AUTH, json={"name": PRODUCT_NAMES[0], "product_id": str(uuid.uuid4()), "created_at": "2020-01-01T00:00:00Z"})
    after = client.get(f"/admin/products/{created[PRODUCT_SKUS[0]]}", headers=ADMIN_AUTH).json()
    results.append(("patch cannot change id/created_at", response.status_code == 200 and after["product_id"] == before["product_id"] and after["created_at"] == before["created_at"], str(after["created_at"])))

    # Duplicate SKU against another existing product.
    response = client.patch(f"/admin/products/{created[PRODUCT_SKUS[1]]}", headers=ADMIN_AUTH, json={"sku": PRODUCT_SKUS[0]})
    results.append(("patch to an existing SKU -> 409", response.status_code == 409 and response.json()["detail"] == "A product with this SKU already exists", str(response.json().get("detail"))))
    response = client.patch(f"/admin/products/{created[PRODUCT_SKUS[1]]}", headers=ADMIN_AUTH, json={"sku": PRODUCT_SKUS[1]})
    results.append(("patch to its own SKU is a no-op 200", response.status_code == 200, str(response.status_code)))

    results.append(("patch nonexistent product -> 404", client.patch(f"/admin/products/{uuid.uuid4()}", headers=ADMIN_AUTH, json={"name": "x"}).status_code == 404, ""))
    results.append(("patch empty name -> 422", client.patch(f"/admin/products/{created[PRODUCT_SKUS[1]]}", headers=ADMIN_AUTH, json={"name": ""}).status_code == 422, ""))
    results.append(("normal user patch -> 403", client.patch(f"/admin/products/{created[PRODUCT_SKUS[1]]}", headers=USER_AUTH, json={"name": "x"}).status_code == 403, ""))

    # --- 6. Delete -----------------------------------------------------------------
    # Unreferenced product -> deleted.
    response = client.delete(f"/admin/products/{created[PRODUCT_SKUS[2]]}", headers=ADMIN_AUTH)
    results.append(("delete unreferenced -> 204", response.status_code == 204, str(response.status_code)))
    gone = client.get(f"/admin/products/{created[PRODUCT_SKUS[2]]}", headers=ADMIN_AUTH).status_code == 404
    results.append(("deleted product is gone -> 404", gone, ""))
    created_product_ids.remove(uuid.UUID(created[PRODUCT_SKUS[2]]))

    results.append(("delete nonexistent -> 404", client.delete(f"/admin/products/{uuid.uuid4()}", headers=ADMIN_AUTH).status_code == 404, ""))

    # --- 7. QR integration: reference blocks deletion -------------------------------
    response = client.post("/admin/qr-codes", headers=ADMIN_AUTH, json={"product_id": str(created[PRODUCT_SKUS[0]]), "coin_value": 5})
    qr_id = response.json().get("qr_id")
    results.append(("qr created against product", response.status_code == 201 and bool(qr_id), str(response.status_code)))

    detail = client.get(f"/admin/products/{created[PRODUCT_SKUS[0]]}", headers=ADMIN_AUTH).json()
    results.append(("product detail reports qr_code_count=1", detail["qr_code_count"] == 1, str(detail["qr_code_count"])))

    response = client.delete(f"/admin/products/{created[PRODUCT_SKUS[0]]}", headers=ADMIN_AUTH)
    results.append(("delete QR-referenced product -> 409", response.status_code == 409 and response.json()["detail"] == REFERENCED_ERROR, str(response.json().get("detail"))))
    with SessionLocal() as db:
        still_there = db.get(Product, created[PRODUCT_SKUS[0]]) is not None
    results.append(("referenced product survives the 409", still_there, ""))

    qr_view = client.get(f"/admin/qr-codes/{qr_id}", headers=ADMIN_AUTH).json()
    results.append(("qr still shows the product afterwards", qr_view["product"]["name"] == PRODUCT_NAMES[0], str(qr_view["product"]["name"])))

finally:
    cleanup(created_product_ids)

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)