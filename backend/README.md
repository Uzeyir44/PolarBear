# CCI / MyColaBear — Backend API

Backend for the CCI / MyColaBear application built with **FastAPI**, **SQLAlchemy 2.0**, **PostgreSQL**, and **Alembic**.

## Stack

| Piece | Tool |
| --- | --- |
| Web framework | FastAPI (with Uvicorn as the ASGI server) |
| ORM | SQLAlchemy 2.0 (typed `Mapped`/`mapped_column` models) |
| Database | PostgreSQL (via `psycopg` driver) |
| Migrations | Alembic |
| Settings | Pydantic v2 + `pydantic-settings` (from `.env`) |
| Password hashing | `pwdlib` with Argon2id |
| JWT | PyJWT (HS256, signed with the secret in `.env`) |
| Input/output validation | Pydantic schemas |

## Setup

1. Create a PostgreSQL database:
   ```sql
   CREATE DATABASE cci_db;
   CREATE EXTENSION IF NOT EXISTS citext;
   ```
2. Copy the values into `.env`:
   ```
   DATABASE_URL=postgresql+psycopg://postgres:<password>@localhost:5432/cci_db
   SECRET_KEY=<random secret string>
   ACCESS_TOKEN_EXPIRE_MINUTES=30
   ```
   - `SECRET_KEY` — signs JWT access tokens. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(64))"`. It must stay secret and is gitignored (never commit `.env`).
   - `ACCESS_TOKEN_EXPIRE_MINUTES` — how many minutes a login token stays valid before the client must log in again.
3. Run database migrations:
   ```bash
   venv/Scripts/alembic upgrade head
   ```

## Running the server

```bash
venv/Scripts/uvicorn app.main:app --reload
```

- Interactive API docs (Swagger UI): http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### Running the admin panel frontend

```bash
cd ../admin-frontend
npm install      # first time only
npm run dev      # http://localhost:5173 (proxies /auth and /admin to :8000)
```

## Project structure

```
app/
├── main.py               # FastAPI app; mounts routers; health check
├── dependencies.py       # get_current_user() — reusable JWT auth gate
├── core/
│   ├── config.py         # Settings (reads DATABASE_URL, SECRET_KEY from .env)
│   ├── database.py       # engine, SessionLocal, Base, get_db() dependency
│   ├── security.py       # Argon2id hashing (PasswordHasher, hash/verify_password)
│   └── jwt.py            # create/decode access tokens (PyJWT)
├── models/               # SQLAlchemy ORM models — 19 tables (complete schema)
├── schemas/
│   ├── user.py           # UserRegister/UserUpdate (input), UserRead/UserPublic (output)
│   ├── token.py          # LoginRequest (input), Token (output)
│   ├── qr.py             # QRCodeRedeemRequest (input), QRCodeRedemptionResult (output)
│   ├── coin.py           # CoinBalance / CoinTransactionRead / QRTransactionReference (output)
│   ├── admin.py          # AdminUserRead (output) — GET /admin/me
│   ├── admin_qr.py       # QR admin input/output schemas
│   ├── product.py        # Product admin input/output schemas
│   ├── user_admin.py     # User admin input/output schemas
│   ├── clothing_admin.py # Clothing admin input/output schemas
│   ├── clothing.py       # ClothingItemRead / ClothingCategoryRef / ClothingItemList (output)
│   ├── wardrobe.py       # WardrobeEntryRead / WardrobeList / EquipmentRead /
│   │                     # EquipResult / UnequipResult (output)
│   └── avatar.py         # AvatarRead / AvatarEquipmentMap / AvatarSlotEquipment (output)
├── routers/
│   ├── auth.py           # POST /auth/register, POST /auth/login
│   ├── users.py          # /users/me, /users/me/coins, /users/me/transactions,
│   │                     # PATCH /users/me, /users/search, follow/unfollow/follow-status
│   ├── qr.py             # POST /qr/redeem — Step 3: validate + redeem + award coins (protected)
│   ├── clothing.py       # GET /clothing — browse the shop (authenticated, paginated,
│   │                     # category filter, AVAILABLE-only)
│   ├── wardrobe.py       # GET /wardrobe — owned clothing; POST|DELETE
│   │                     # /wardrobe/{wardrobe_id}/equip — equip/unequip
│   ├── avatar.py         # GET /avatar — the caller's avatar + full equipment state
│   └── admin/            # Internal admin console (every route behind get_current_admin)
│       ├── __init__.py   # /admin/me + mounts /admin/qr-codes, /admin/users,
│       │                 # /admin/products and /admin/clothing
│       ├── qr_codes.py   # /admin/qr-codes CRUD + status management, /admin/.../products
│       ├── users.py      # /admin/users list/detail/activate-deactivate
│       ├── products.py   # /admin/products CRUD with guarded delete
│       └── clothing.py   # /admin/clothing CRUD + categories lookup, guarded delete
tests/                   # End-to-end test scripts (run with python -m tests.<name>)
├── test_db.py            # Manual DB connectivity check
├── test_register.py      # End-to-end registration checks
├── test_auth_flow.py     # End-to-end login + JWT checks
├── test_update_profile.py# End-to-end PATCH /users/me checks
├── test_user_search.py   # End-to-end GET /users/search checks
├── test_follow.py        # End-to-end follow/unfollow/follow-status checks
├── test_qr_redeem.py     # End-to-end POST /qr/redeem redeem checks
├── test_qr_redeem_coins.py# End-to-end coin-award checks (Step 3)
├── test_user_coins.py    # End-to-end GET /users/me/coins and /users/me/transactions
├── test_admin_qr.py      # End-to-end admin auth + QR management checks
├── test_admin_users.py   # End-to-end admin auth + user management checks
├── test_admin_products.py# End-to-end admin product-management checks
├── test_clothing_browse.py # End-to-end GET /clothing browse checks
├── test_clothing_purchase.py # End-to-end POST /clothing/{item_id}/purchase checks
├── test_wardrobe.py        # End-to-end GET /wardrobe owned-clothing checks
├── test_wardrobe_equip.py  # End-to-end equip/unequip checks (ownership, slots,
│                           # replacement, unequip edge cases, availability,
│                           # coin safety, concurrency)
├── test_avatar.py            # End-to-end GET /avatar checks (auth, missing
│                             # avatar 404, empty slots, one/multiple equipped
│                             # items, replacement, user isolation,
│                             # availability independence, data safety)
└── test_admin_clothing.py  # End-to-end admin auth + clothing-management checks
alembic/                  # Migrations (initial_schema, users.is_admin flag, clothing seed,
                          # ensure-users-have-avatars backfill)
```

## What's implemented so far

### Database layer
- SQLAlchemy `engine` bound to `settings.database_url`.
- `SessionLocal` session factory (`autoflush=False`, `autocommit=False`).
- `Base` declarative base for ORM models.
- `get_db()` FastAPI dependency that yields a session and **always closes it** in a `finally` block — used by every endpoint that talks to the database.
- Alembic migrations with the full 19-table schema (users, avatars, wardrobe, coins, QR codes, competitions, votes, notifications, etc.).

### User registration (`POST /auth/register`)

Request body:

```json
{
  "username": "polar_bear",
  "email": "user@example.com",
  "password": "a-strong-password"
}
```

Validation (Pydantic, `UserRegister`):
- `username` — 3–30 chars, letters/digits/underscore only.
- `email` — must be a valid email format.
- `password` — 8–128 chars.

Flow:
1. Validate the request with Pydantic.
2. Refuse if the username or email already exists (`409 Conflict`). Because the columns use `CITEXT`, uniqueness is case-insensitive (`Alice` and `alice` collide).
3. Hash the password with Argon2id (never stored as plain text).
4. Create the user row **and their avatar row** and commit both in **one transaction** — see the invariant below.
5. Handle database-level unique-violations safely as a fallback (race condition) by mapping the violated index to a clear `409`.
6. Return only safe user info via `UserRead` — the response **never** includes `password_hash` or the password.

**Invariant: every registered user receives exactly one avatar.** The avatar is created in the *same* database transaction as the user (`BEGIN → INSERT user → INSERT avatar → COMMIT`). If anything fails, both roll back together, so this flow can never leave a committed user without an avatar. `avatars.user_id` is `UNIQUE`, so a user can never own two avatars. Users registered before this behavior existed were backfilled by migration `5a59fd52b390` ("ensure users have avatars"), which inserts one avatar for every user that doesn't already have one (idempotent — it can never create duplicates).

Example response (`201 Created`):

```json
{
  "user_id": "42f9c0a8-…",
  "username": "polar_bear",
  "email": "user@example.com",
  "is_active": true,
  "created_at": "2026-08-17T12:00:00Z"
}
```

### Health check (`GET /health/db`)
Runs `SELECT 1` against PostgreSQL through FastAPI → SQLAlchemy → DB to confirm the full connectivity stack works. Returns `{"database": "ok", "result": 1}`.

### Login (`POST /auth/login`)
Authenticates the user and issues a JWT access token.

Request body (`LoginRequest`):

```json
{
  "username": "polar_bear",
  "email": "user@example.com",
  "password": "a-strong-password"
}
```

The `username` field accepts either a username or an email.

Flow:
1. Find the user by username or email (CITEXT → case-insensitive).
2. Verify the password against the stored Argon2 hash with `verify_password()`.
3. Fail with `401 Unauthorized` on bad credentials. The same message is used for "user not found" and "wrong password" so attackers can't enumerate accounts.
4. On success, sign a JWT with PyJWT (HS256) and return it.

Response (`200 OK`):

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
  "token_type": "bearer"
}
```

### Token contents (`app/core/jwt.py`)

The JWT carries only three claims — no password, no hash, no profile data:

- `sub` — the user's `user_id` (the "subject" of the token; this is how the backend identifies the user).
- `iat` — issued-at timestamp.
- `exp` — expiry timestamp (`now + ACCESS_TOKEN_EXPIRE_MINUTES`). PyJWT refuses to decode an expired token.

The token is signed with `SECRET_KEY` using HS256. Anyone with the secret can mint tokens, so it must never leak or be committed.

### Protecting endpoints (`app/dependencies.py`)

`get_current_user()` is a reusable dependency that any endpoint can declare:

```python
current_user: User = Depends(get_current_user)
```

It: reads the `Authorization: Bearer <token>` header → decodes and validates the JWT → extracts `sub` as a `UUID` → loads the user from Postgres → returns the `User`. If the token is missing, invalid, expired, or belongs to a nonexistent/inactive user, it raises `401 Unauthorized`.

### Current user (`GET /users/me`)

The first protected endpoint — the pattern for every future one. Requires `Authorization: Bearer <token>` and returns the same safe `UserRead` shape as registration.

### Update profile (`PATCH /users/me`)

Lets the authenticated user update their own profile. Requires `Authorization: Bearer <token>`; without it (or with an invalid/expired token) it returns `401`.

Request body (`UserUpdate`) — every field is optional, so the client sends only what it wants to change:

```json
{
  "username": "new_username"
}
```

```json
{
  "biography": "My new biography"
}
```

```json
{
  "username": "new_username",
  "biography": "My new biography",
  "profile_picture_url": "https://example.com/avatar.jpg"
}
```

Updatable fields:

| Field | Rules |
| --- | --- |
| `username` | Same constraints as registration (3–30 chars, letters/digits/underscore). Must be unique (`409` on a duplicate, case-insensitive because of `CITEXT`). Can't be `null` — sending `{"username": null}` is a `422` validation error. |
| `biography` | Optional text, max 500 chars. Send `null` to clear it. |
| `profile_picture_url` | Optional URL string, max 2048 chars. Send `null` to clear it. |

Flow:
1. Validate the body with Pydantic (`UserUpdate`); invalid data → `422`.
2. Authenticate through `get_current_user()`; failure → `401`.
3. `payload.model_dump(exclude_unset=True)` — a dict containing **only** the fields the client actually sent. Omitted fields are never written, so a single-field PATCH leaves the others untouched. Sending explicit `null` is a value, so it clears `biography`/`profile_picture_url`.
4. If `username` is present, pre-check the unique constraint with a `select`. Reusing your own username is allowed; a username owned by another user → `409 Conflict`.
5. Apply the supplied fields, `commit()`, then `refresh()` and return the row.
6. A database `IntegrityError` (a concurrent request grabbed the username between the check and the commit) is rolled back and mapped to the same `409`.

Protected fields (`user_id`, `email`, `password_hash`, `coin_balance`, `winning_streak`, `is_active`, `created_at`) are **not** part of `UserUpdate` — they are owned by other parts of the system and can never be changed through this endpoint.

Response is the same safe `UserRead` shape as `GET /users/me` and registration (now includes `biography` and `profile_picture_url`) — `password_hash` is never exposed.

### User search (`GET /users/search?q=<query>`)

Lets any authenticated user find **other** users by partial username match. The caller is always excluded from their own results. Requires `Authorization: Bearer <token>`.

Query parameters (validated by FastAPI):

| Param | Rules |
| --- | --- |
| `q` | Required. 1–30 chars. Matches a username that **contains** the fragment anywhere (prefix, middle, or suffix). Case-insensitive. Missing/empty/too-long → `422`. |
| `limit` | Optional, default `20`, min `1`, max `20`. Caps how many results are returned. Out of range → `422`. |

Example:

```
GET /users/search?q=alex
Authorization: Bearer <JWT>
```

```json
[
  {
    "user_id": "…",
    "username": "alex123",
    "profile_picture_url": null,
    "biography": "Hello!"
  },
  {
    "user_id": "…",
    "username": "alex_dev",
    "profile_picture_url": "https://example.com/avatar.jpg",
    "biography": null
  }
]
```

Flow:
1. Validate `q`/`limit` via `Query` constraints → `422` on bad input.
2. Authenticate through `get_current_user()`; failure → `401`.
3. Build the pattern `%q%` with `ILIKE` (case-insensitive; the `CITEXT` column already folds case, this just makes the intent explicit) and escape `%`, `_`, `\` so the query is matched literally.
4. `select(User)` filtered by active, not-self, and the `ILIKE` match, ordered by username and `LIMIT`ed to `limit`.
5. Return the rows through the `UserPublic` response model.

Response uses a dedicated **`UserPublic`** schema that exposes exactly four fields — `user_id`, `username`, `profile_picture_url`, `biography`. It deliberately omits `email`, `is_active`, `created_at`, and never contains `password_hash`, `coin_balance`, or `winning_streak`.

Search strategy tradeoff: a `%q%` (substring-anywhere) pattern **cannot use the btree index** on `users.username` — the btree only accelerates exact and `q%` prefix matches via range scans. `%q%` therefore does a sequential scan. That's fine at this project's scale; the upgrade path if the user table grows large (≈100k+ rows) is the `pg_trgm` extension plus a GIN index with `gin_trgm_ops` on `username`. It was deliberately **not** added now because it costs extra disk space and slower writes for no benefit at the current size. (Full reasoning is in `app/routers/users.py`.)

## Follow / Unfollow

Three protected endpoints let an authenticated user follow another user, stop following them, and check whether they currently follow them. The database already contained the `follows` table (composite primary key `(follower_id, followee_id)`, a CHECK constraint blocking self-follows, `created_at`, and cascade deletes on both FKs) — **no schema change or migration was needed**.

### Follow a user (`POST /users/{user_id}/follow`)

Creates a follow relationship from the authenticated user to the user named in the URL.

Flow:
1. Authenticate via `get_current_user()`; no/invalid/expired token → `401`.
2. FastAPI parses `user_id` from the URL as a `uuid.UUID`.
3. Load the target user with `db.get(User, user_id)`; missing → `404`.
4. Reject inactive targets → `400` (deactivated accounts can't be followed).
5. Reject following yourself → `400` (also guaranteed by the DB CHECK constraint `ck_follows_no_self_follow`).
6. Check for an existing row with `db.get(Follow, (current_user.user_id, user_id))`; already following → `409`.
7. Insert a `Follow(follower_id=current_user.user_id, followee_id=user_id)` and commit.
8. On a DB `IntegrityError` (a concurrent request inserted the same row between the check and the commit) → rollback and return the same `409`.

Response (`201 Created`):

```json
{ "is_following": true }
```

### Unfollow a user (`DELETE /users/{user_id}/follow`)

Removes the follow relationship if it exists.

Flow:
1. Authenticate via `get_current_user()`; failure → `401`.
2. Look up the row with `db.get(Follow, (current_user.user_id, user_id))`; not following → `404` (this also covers a target that doesn't exist — a follow row can't point at a deleted user because of `ON DELETE CASCADE`).
3. `db.delete(follow)` and commit.

Response (`200 OK`):

```json
{ "is_following": false }
```

### Follow status (`GET /users/{user_id}/follow-status`)

Tells the authenticated user whether they currently follow the target.

Flow:
1. Authenticate via `get_current_user()`; failure → `401`.
2. Return `true` if a `Follow` row exists for `(current_user.user_id, user_id)`, else `false`.

Response (`200 OK`):

```json
{ "is_following": true }
```

### Why the follower always comes from the JWT

The endpoints declare **no request body**. The follower is derived only from:

```
Authorization: Bearer <JWT>
   ↓
get_current_user()
   ↓
current_user.user_id
   ↓
follower_id  (in the Follow row)
```

The **only** user ID the client controls is the followee in the URL (`user_id`). If a client sends a body like `{"follower_id": "<someone else>"}`, FastAPI simply ignores it — there is no field to set it from, so a follower can never be forged.

### How duplicate follows are prevented

Two layers:
1. **Database level (the real guarantee):** the `follows` table's composite primary key on `(follower_id, followee_id)` makes a second identical follow structurally impossible — the INSERT would violate the PK.
2. **Application level (the friendly error):** the endpoint pre-checks the same tuple with `db.get(Follow, ...)` and returns `409 Conflict` instead of letting the raw constraint error surface. The `IntegrityError` fallback covers the race where two concurrent requests pass the pre-check before either commits.

## QR code redemption — Step 3: validate, redeem, and award coins

Redemption end to end: `POST /qr/redeem` validates the submitted code (exists, `ACTIVE`, not expired), **claims it for the authenticated user** (`status = REDEEMED`, `redeemed_by_user_id = <caller>`, `redeemed_at = now`), **credits the user's coins**, and writes the matching `coin_transactions` ledger row — all in **one database transaction** that commits (or rolls back) as a unit.

### Redeem a code (`POST /qr/redeem`)

Requires `Authorization: Bearer <token>`. Request body (`QRCodeRedeemRequest`):

```json
{ "code": "COLA-123456" }
```

Flow:
1. Authenticate via `get_current_user()`; no/invalid/expired token → `401`.
2. Pydantic validates `code` (1–64 chars) → `422` on bad input.
3. Lock the row with `select(QRCode).where(QRCode.code == ...).with_for_update()` (`SELECT … FOR UPDATE`); unknown code → `404`.
4. While holding the lock, check status: `REDEEMED` → `409 Conflict` ("Code has already been redeemed"); `EXPIRED`, **or** an `ACTIVE` code whose `expires_at` has passed → `410 Gone` ("Code has expired").
5. Mark the code `REDEEMED`, set `redeemed_by_user_id`/`redeemed_at`.
6. Credit the user with a single atomic `UPDATE users SET coin_balance = coin_balance + <coin_value> RETURNING coin_balance`.
7. Look up the `qr_redemption` type from `coin_transaction_types` and insert the ledger row (`user_id`, `type_id`, `amount = coin_value`, `balance_after = new balance`, `qr_id`).
8. `db.commit()` — the claim, credit, and ledger insert all become permanent together. (If any of those steps raised instead, nothing commits and the session close rolls the whole transaction back.)
9. Return a response exposing only the message, the coins earned, and the new balance.

Response (`200 OK`) — exposes only `message`, `coins_earned`, `balance`:

```json
{
  "message": "Code redeemed successfully",
  "coins_earned": 10,
  "balance": 150
}
```

Internal fields (`qr_id`, `product_id`, `coin_value`, `status`, `redeemed_by_user_id`, `redeemed_at`, `expires_at`, `created_at`) are **not** returned.

### Why one transaction + row locking

The claim, the balance credit, and the ledger insert are deliberately committed **together**. On a failure anywhere in between, both of these impossible states are prevented: "QR redeemed but user got no coins" and "user got coins but no ledger row". Because `get_db()` closes the session in a `finally` block, an exception before `commit()` rolls the whole transaction back.

A code must be redeemable exactly once, and a naive "load the row, check `status == 'ACTIVE'` in Python, then write" can race: two requests can both read `active`, both pass the check, and both award coins. The endpoint therefore takes an exclusive **row lock** with `SELECT … FOR UPDATE` **before** checking the status. Postgres holds that lock until the transaction ends, so the two requests serialize: the first locks the row, redeems, and commits; the second blocks on the lock, then re-reads the now-`REDEEMED` row and returns `409`. The Python status check is only reliable *because* it happens under the lock.

The user's `coin_balance` is bumped with one atomic `coin_balance = coin_balance + n` statement rather than `user.coin_balance += n` in Python. That prevents a *lost update* when the **same user** redeems two **different** codes concurrently — a read-modify-write would let the second request overwrite the first request's credit using a stale value.

### What this step does NOT do (deliberately)

- It does **not** generate QR/barcodes or provide admin code creation.
- It does **not** implement clothing purchases, voting, or competition rewards.

### Timezone note

The `qr_codes` timestamp columns are `timestamp without time zone` (timezone-naive). `redeemed_at` is written as naive UTC and all stored timestamps are treated as UTC when compared. Keep that convention when admin code creation is implemented.

## Coin balance & transaction history

Two read-only endpoints let the authenticated user see their own coins. Both are read-only — nothing here changes the balance or the ledger — and both derive the user **only** from the JWT:

```
Authorization: Bearer <JWT>
   ↓
get_current_user()
   ↓
current_user.user_id
```

Neither endpoint accepts a `user_id` from the client (no path/query/body parameter), so one user can never read another user's money.

### Current balance (`GET /users/me/coins`)

Returns the cached `coin_balance` already loaded by `get_current_user()` (`users.coin_balance` is a cache of the ledger; the ledger rows are the source of truth). No extra query, no writes.

Response (`200 OK`):

```json
{ "balance": 150 }
```

### Transaction history (`GET /users/me/transactions`)

Returns the authenticated user's `coin_transactions` ledger rows, newest first, paginated with `limit` (default 20, valid 1–50) and `offset` (default 0):

```
GET /users/me/transactions?limit=20&offset=0
Authorization: Bearer <JWT>
```

- Newest transactions appear first (`ORDER BY created_at DESC`, tie-broken by `transaction_id`).
- The query filters `WHERE user_id = current_user.user_id` and the existing `ix_coin_transactions_user_id_created_at (user_id, created_at)` composite index serves exactly this filter-plus-sort pattern (a backward index scan), so the database never sorts the user's whole history.
- `limit`/`offset` are validated by FastAPI (`ge`/`le`) → invalid values are `422`.
- An empty history returns `200` with `[]`.

Each item resolves the ledger's internal ids into something the client can use. `transaction_type` and `direction` come from the joined `coin_transaction_types` lookup row; when the transaction was a QR redemption, the `qr` object carries the scanned code and its product. Reference fields for not-yet-implemented features (competition/wardrobe/vote) are nullable and currently null. Internal fields (`user_id`, `type_id`, raw relationship objects) are never exposed.

Response (`200 OK`) — `transaction_type`/`direction`/`qr` populated because this row came from redeeming a QR code:

```json
[
  {
    "transaction_id": "e1b49c0a-…",
    "amount": 30,
    "balance_after": 130,
    "transaction_type": "qr_redemption",
    "direction": "CREDIT",
    "created_at": "2026-08-20T08:46:57.966818",
    "qr": {
      "qr_id": "fad47fec-…",
      "code": "COLA-ABC123",
      "product_name": "Cola 330ml"
    },
    "competition_id": null,
    "wardrobe_id": null,
    "vote_id": null
  }
]
```

### Security notes

- The response schemas (`CoinBalance`, `CoinTransactionRead`, `QRTransactionReference` in `app/schemas/coin.py`) are output-only — they define exactly which fields can appear, so a leaked internal column would be dropped by FastAPI's `response_model` filtering even if it were ever returned.
- The query always filters by `current_user.user_id`; there is no code path that reads another user's rows.

## Clothing shop — browse (`GET /clothing`)

The first Phase 3 (clothing shop) endpoint: an authenticated client browses the catalog with pagination and an optional category filter. Requires `Authorization: Bearer <token>`; without a valid token → `401`.

Query parameters:

| Param | Rules |
| --- | --- |
| `category_id` | Optional. A `clothing_categories.category_id` (`SMALLINT`, 1–32767). Returns only items from that category. A nonexistent in-range id → `404`; a non-integer, out-of-range, or non-positive value → `422`. |
| `limit` | Optional, default `20`, min `1`, max `100`. Out of range → `422`. |
| `offset` | Optional, default `0`, min `0`. Negative → `422`. |

Example:

```
GET /clothing?category_id=3&limit=2&offset=0
Authorization: Bearer <JWT>
```

Response (`200 OK`):

```json
{
  "items": [
    {
      "item_id": "…",
      "name": "Polar Hoodie",
      "description": "Heavyweight fleece with a hidden pocket.",
      "category": { "category_id": 3, "category_name": "Tops", "slot": "top" },
      "price": 500,
      "image_url": "https://mycolabear.example.com/clothing/tops/polar-hoodie.png",
      "availability_status": "available",
      "collection_id": null
    }
  ],
  "total": 12,
  "limit": 2,
  "offset": 0
}
```

The response is whitelisted by `ClothingItemRead` (`app/schemas/clothing.py`): only `item_id`, `name`, `description`, `category` (nested id/name/slot), `price`, `image_url`, `availability_status`, and `collection_id` are exposed — no FK housekeeping, no `created_at`, nothing internal.

**Availability rule.** Browsing shows **only `AVAILABLE` items**. `UNAVAILABLE` items are sold out / withdrawn catalog entries (the design doc says to set `availability_status='unavailable'` instead of deleting them) and `UPCOMING` items are not purchasable yet — neither appears in the shop shelf, and only AVAILABLE items count towards `total`. This is an explicit product decision (the design doc itself does not state a browse rule); see `app/routers/clothing.py` for the full reasoning.

**Ordering / performance.** Results are deterministic: `ORDER BY created_at DESC, item_id DESC`, so repeated requests over the same data paginate stably. The category context is loaded in the same query (`joinedload`), so there is no N+1. No schema change was required: both `clothing_items` and `clothing_categories` already existed; the catalog entries (see **Seed data** below) were added by a data-only migration (`e833bac26dfc`).

## Clothing shop — purchase (`POST /clothing/{item_id}/purchase`)

The second Phase 3 (clothing shop) endpoint: an authenticated user buys one catalog item. Requires `Authorization: Bearer <token>`; without a valid token → `401`. There is **no request body** — the item comes from the URL path, and everything else (price, ledger amount, balance) is read from the database, so a client can never under-report what it pays.

One purchase = one atomic transaction that either commits completely or not at all:

1. **Lock the buyer's row** (`SELECT … FOR UPDATE` on `users`) — serializes all of a user's concurrent debits.
2. **Validate the item** — must exist (`404`) and be `AVAILABLE`; `UNAVAILABLE`/`UPCOMING` items are rejected with `409`.
3. **Ownership check** — already owning the item → `409` (the `uq_user_wardrobe_no_duplicate_purchase` unique constraint remains the final race protection; an `IntegrityError` from it is caught and reported as the same `409`).
4. **Balance check** — `coin_balance < price` → `400`, nothing is written.
5. **Debit** — atomic guarded `UPDATE users SET coin_balance = coin_balance - :price … WHERE coin_balance >= :price RETURNING coin_balance` (the same pattern QR redemption uses; it cannot produce a negative balance or a lost update even without the lock).
6. **Wardrobe insert** — permanent ownership row in `user_wardrobe`.
7. **Ledger insert** — one `coin_transactions` row with type `clothing_purchase` (the seeded DEBIT lookup row), `amount = -price`, `balance_after` = the post-debit balance, and `wardrobe_id` pointing at the new ownership record.
8. **COMMIT** — all writes land together; any failure before commit rolls all of them back.

Response (`200 OK`):

```json
{
  "message": "Item purchased successfully",
  "wardrobe_id": "…",
  "item": {
    "item_id": "…",
    "name": "Polar Shades",
    "description": "Classic aviator cut with UV400 protection.",
    "category": { "category_id": 6, "category_name": "Sunglasses", "slot": "accessory" },
    "price": 120,
    "image_url": "https://mycolabear.example.com/clothing/sunglasses/polar-shades.png",
    "availability_status": "available",
    "collection_id": null
  },
  "amount_spent": 120,
  "remaining_balance": 380,
  "transaction_id": "…"
}
```

`amount_spent` is a positive number (the debit sign lives in the ledger); `remaining_balance` mirrors `users.coin_balance` after the debit; `transaction_id` is the `coin_transactions` ledger row. The payload is whitelisted by `ClothingPurchaseResult` (`app/schemas/clothing.py`) and reuses the catalog shape for the purchased item — no internal fields, no auth data.

Error cases: missing/invalid/expired token → `401`; malformed uuid in the path → `422`; nonexistent item → `404`; `UNAVAILABLE`/`UPCOMING` item → `409`; already owned → `409`; insufficient coins → `400` (with no database change). A mid-transaction failure (e.g. the lookup row unresolvable) rolls back the debit, the wardrobe insert, and the ledger together — partial state is impossible.

No schema change was required for purchasing: `users.coin_balance`, `clothing_items.price`, `user_wardrobe`, `coin_transactions`, and the seeded `clothing_purchase` transaction type all already existed.

## Wardrobe — owned clothing (`GET /wardrobe`)

The first Phase 4 (wardrobe) endpoint: an authenticated user lists the clothing they own. Requires `Authorization: Bearer <token>`; without a valid token → `401`. Read-only — equipping/unequipping is covered by its own endpoints below.

Query parameters (the project's standard limit/offset pagination):

| Parameter | Default | Constraints | Meaning |
|-----------|---------|-------------|---------|
| `limit`   | 20      | 1–100       | Entries per page |
| `offset`  | 0       | ≥ 0         | Entries to skip |

Response (`200 OK`) — the same `items/total/limit/offset` envelope as `GET /clothing`, where each entry is one `user_wardrobe` ownership record plus its item in the exact catalog shape (`ClothingItemRead`, nesting the category with its slot):

```json
{
  "items": [
    {
      "wardrobe_id": "…",
      "purchased_at": "2026-08-21T12:10:37.961346",
      "item": {
        "item_id": "…",
        "name": "Polar Shades",
        "description": "Classic aviator cut with UV400 protection.",
        "category": { "category_id": 6, "category_name": "Sunglasses", "slot": "accessory" },
        "price": 120,
        "image_url": "https://mycolabear.example.com/clothing/sunglasses/polar-shades.png",
        "availability_status": "available",
        "collection_id": null
      }
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

Behavior and guarantees:

- **Strict data isolation** — the owner comes ONLY from the JWT (`get_current_user()` → `current_user.user_id`) and is applied directly in the SQL `WHERE user_wardrobe.user_id = :jwt_user_id`. The endpoint declares no `user_id` path/query/body parameter, so there is no client input that could redirect the query at another user's rows; a smuggled `?user_id=…` query param is simply ignored by FastAPI.
- **No availability filter** — unlike browsing, the wardrobe does NOT filter on `clothing_items.availability_status`. If an admin later marks an owned item `UNAVAILABLE`/`UPCOMING`, existing owners still see it: availability governs buying, not owning (this is what makes the admin rule "mark it UNAVAILABLE instead of deleting" safe).
- **Deterministic ordering** — `purchased_at DESC` with `wardrobe_id DESC` as the tiebreaker (server-side `now()` can give two purchases committed together the same timestamp), so repeated requests paginate stably.
- **No N+1** — each page loads its items with `joinedload(UserWardrobe.item).joinedload(ClothingItem.category)`: one query for the page plus one cheap `count(*)`.
- **Empty wardrobe** — a user who never purchased gets `200` with `"items": []` and `"total": 0`; an empty closet is valid, never a `404`.
- **Validation** — non-integer or out-of-range `limit`/`offset` → `422`.

The payload is whitelisted by `WardrobeEntryRead`/`WardrobeList` (`app/schemas/wardrobe.py`) reusing `ClothingItemRead` from `app/schemas/clothing.py` — no email, password hash, auth-provider, balance, or other internal fields anywhere in the response.

No schema change was required: the read runs over the existing `user_wardrobe`, `clothing_items`, and `clothing_categories` tables.

## Wardrobe — equip / unequip (`POST|DELETE /wardrobe/{wardrobe_id}/equip`)

The second Phase 4 step: an authenticated user equips or unequips an item they own. Both endpoints require `Authorization: Bearer <token>` (→ `401` otherwise) and take **no request body** — the `wardrobe_id` in the URL path is the only client input.

- `POST /wardrobe/{wardrobe_id}/equip` → `200` with the new slot state.
- `DELETE /wardrobe/{wardrobe_id}/equip` → `200` confirming the cleared slot.

Equip workflow (one transaction):

1. **Ownership lookup** — one query loads the `user_wardrobe` row filtered by (`wardrobe_id` AND the JWT-derived `user_id`) with its item and category eagerly joined. Another user's `wardrobe_id` and a nonexistent one are indistinguishable → `404` "Wardrobe item not found". There is no `user_id`/`avatar_id`/`item_id`/`slot` client input anywhere.
2. **Avatar resolution** — `avatars.user_id` is UNIQUE (0-or-1 per user) and registration creates the avatar in the same transaction as the user, so this lookup always resolves for normally registered users; if it ever doesn't (e.g. a legacy row predating the backfill), it is reported explicitly as `404` "Avatar not found" rather than silently created here — avatar creation belongs to registration, not to a clothing endpoint.
3. **Slot determination** — the slot comes ONLY from `clothing_items.category_id → clothing_categories.slot`. Sunglasses always land in ACCESSORY because their category says so; no client value can override this.
4. **Atomic upsert** — one PostgreSQL statement: `INSERT INTO avatar_equipment (avatar_id, slot, item_id) … ON CONFLICT (avatar_id, slot) DO UPDATE SET item_id = excluded.item_id, equipped_at = now() RETURNING equipped_at`. An empty slot is filled; an occupied slot is REPLACED. The displaced item stays owned in `user_wardrobe`; nothing is charged and no ledger row is written — purchasing and equipping are separate operations.

Response of equip (`200 OK`):

```json
{
  "message": "Item equipped successfully",
  "equipment": {
    "avatar_id": "…",
    "slot": "accessory",
    "equipped_at": "2026-08-21T12:34:09.127468",
    "item": {
      "item_id": "…",
      "name": "Polar Shades",
      "description": "Classic aviator cut with UV400 protection.",
      "category": { "category_id": 6, "category_name": "Sunglasses", "slot": "accessory" },
      "price": 120,
      "image_url": "https://mycolabear.example.com/clothing/sunglasses/polar-shades.png",
      "availability_status": "available",
      "collection_id": null
    }
  }
}
```

Unequip deletes ONLY the row where THIS item occupies THIS slot of THIS avatar (`DELETE … WHERE avatar_id = … AND slot = … AND item_id = …`). If a different item is currently equipped there — or the slot is empty — nothing matches → `409` "This item is not currently equipped", so unequipping Shirt B can never remove Shirt A. The wardrobe record always remains owned. Response:

```json
{
  "message": "Item unequipped successfully",
  "avatar_id": "…",
  "slot": "top"
}
```

Guarantees:

- **Concurrency** — the `avatar_equipment` primary key `(avatar_id, slot)` remains the structural one-item-per-slot guarantee. Two concurrent equips into the same slot serialize on that index entry inside the upsert and exactly one row survives (last writer wins, which is the intended semantics); there is no read-modify-write race because each request fully specifies the final state.
- **Availability independence** — no availability check exists on either endpoint. An admin marking an owned/equipped item `UNAVAILABLE`/`UPCOMING` neither unequips it nor blocks managing it: availability governs buying, not owning or wearing.
- **Coin safety** — equipping and unequipping never touch `users.coin_balance` and never create `coin_transactions` rows.
- **Error cases** — missing/invalid/expired token → `401`; malformed uuid in the path → `422`; wardrobe row not found or owned by someone else → `404`; user has no avatar → `404` "Avatar not found"; unequip of an item not currently equipped in its slot → `409`.

No schema change was required: everything runs over the existing `avatars`, `user_wardrobe`, `clothing_items`, `clothing_categories`, and `avatar_equipment` tables with their original constraints.

## Avatar (`GET /avatar`)

Phase 5: an authenticated user retrieves **their** avatar together with its complete currently-equipped clothing in ONE response — everything the mobile client needs to render the avatar, with no request per slot. Requires `Authorization: Bearer <token>`; without a valid token → `401`. There are **no request parameters** of any kind.

This endpoint is read-only and exposes state; it does not customize anything. Equipping/unequipping stays with the wardrobe endpoints (`POST|DELETE /wardrobe/{wardrobe_id}/equip`), which remain the only writers of `avatar_equipment`:

```
wardrobe equip/unequip → avatar_equipment → GET /avatar → mobile rendering
```

Response (`200 OK`) — the equipment map always contains all six `AvatarSlot` keys; an empty slot is an explicit `null`:

```json
{
  "avatar_id": "…",
  "equipment": {
    "hair": {
      "equipped_at": "2026-08-21T12:40:11.302114",
      "item": {
        "item_id": "…",
        "name": "Classic Crew Cut",
        "description": "A timeless, low-maintenance trim.",
        "category": { "category_id": 1, "category_name": "Hairstyles", "slot": "hair" },
        "price": 250,
        "image_url": "https://mycolabear.example.com/clothing/hair/classic-crew-cut.png",
        "availability_status": "available",
        "collection_id": null
      }
    },
    "hat": null,
    "top": { "equipped_at": "…", "item": { "…": "…" } },
    "bottom": { "equipped_at": "…", "item": { "…": "…" } },
    "shoes": { "equipped_at": "…", "item": { "…": "…" } },
    "accessory": null
  }
}
```

Each occupied slot is `{ equipped_at, item }`, where `item` is the exact canonical catalog shape (`ClothingItemRead`, nesting the category with its slot) used by browse/purchase/wardrobe/equip — one item payload across the whole app. The payload is whitelisted by `AvatarRead` / `AvatarEquipmentMap` / `AvatarSlotEquipment` (`app/schemas/avatar.py`): no email, password hash, auth-provider, balance, streak or admin fields anywhere.

Behavior and guarantees:

- **Strict data isolation** — the avatar comes ONLY from the JWT (`get_current_user()` → `current_user.user_id`) filtered directly in the SQL `WHERE avatars.user_id = :jwt_user_id`. The endpoint declares no `user_id`/`avatar_id` input, so a smuggled `?user_id=…` query parameter is simply ignored by FastAPI; one user can never read another user's avatar.
- **Missing avatar** — `avatars.user_id` is UNIQUE (0-or-1 per user) and registration creates the avatar in the same transaction as the user, so every normally registered user has one and `GET /avatar` succeeds immediately after register + login. If a missing avatar is ever seen anyway (e.g. a legacy row predating migration `5a59fd52b390`), it is reported as an explicit `404` "Avatar not found" — the same convention as equip/unequip. The GET endpoint never silently creates an avatar.
- **No N+1** — the avatar, its equipment rows, their items, and the items' categories load in ONE query (`joinedload(Avatar.equipment) → joinedload(AvatarEquipment.item) → joinedload(ClothingItem.category)`). An avatar has at most six equipment rows (the `(avatar_id, slot)` primary key), so there are no per-slot queries.
- **Slot authority** — slots come from `avatar_equipment.slot`, which the equip flow derived from `clothing_items.category_id → clothing_categories.slot` at equip time; nothing recomputes them here.
- **Availability independence** — NO filter on `availability_status`: if an admin marks an equipped item `UNAVAILABLE`/`UPCOMING`, the avatar still reports it (with its current status). Availability governs buying, not wearing — mirroring the wardrobe rule.
- **Empty slots** — all six keys are always present; empty means explicit `null`, never a missing key or a placeholder record.
- **Read-only** — no writes, no coin balance or ledger interaction.

Error cases: missing/invalid/expired token → `401`; user has no avatar → `404` "Avatar not found".

No schema change was required: the read runs over the existing `avatars`, `avatar_equipment`, `clothing_items`, and `clothing_categories` tables.

## Competition requests (Phase 6, Part 1)

The pre-competition negotiation lifecycle: send a challenge to another user, see the requests addressed to you / sent by you, and **accept**, **decline**, or **cancel** them. This stage only touches the `competition_requests` table — a request is `pending`/`accepted`/`declined`/`cancelled` and has a duration but **no prize pool, no votes, no timer**. Accepting a request does **not** create a competition, does **not** move coins, and does **not** write to `coin_transactions`.

All six endpoints require `Authorization: Bearer <token>`; missing/invalid/expired token → `401`. Inactive accounts are blocked by the existing `get_current_user()` dependency (inactive → `401`), so an inactive user can neither send requests nor act on them.

**Authorization model** — the acting participant is ALWAYS `current_user.user_id` from the JWT, never anything the client sends:

- Sending: the challenger is written by the backend; the client only supplies `opponent_id` + `duration_minutes`. A smuggled `challenger_id`/`status`/`created_at`/`responded_at` in the body is simply ignored.
- Accept / decline: **only the opponent** (the request's recipient) may act.
- Cancel: **only the challenger** (the request's sender) may act.

**Status lifecycle** — a request starts `PENDING` and can transition exactly once:

```
PENDING ──┬── ACCEPTED   (opponent accepts)
          ├── DECLINED   (opponent declines)
          └── CANCELLED  (challenger cancels)
```

Once a request leaves `PENDING` it can never transition again; any further accept/decline/cancel attempt → `409 Conflict` ("Competition request is no longer pending"). `responded_at` stays `NULL` while `PENDING` and is set (naive UTC) on the transition.

**Duplicate / simultaneous pending requests** — deliberately allowed, per the documented `competition_requests` design (no uniqueness constraint): a challenger may hold several `PENDING` requests to the same opponent, and `A→B` + `B→A` can both be `PENDING` at once. When a request is later accepted, a future Phase cancels the other pending requests involving either party in the same transaction.

**Concurrency** — accept/decline/cancel use an atomic conditional UPDATE (`UPDATE … SET status = … WHERE request_id = … AND status = 'PENDING' AND <participant> = me`) and verify exactly one row was affected. Under READ COMMITTED Postgres re-evaluates the `WHERE` against the latest committed row version after acquiring the row lock, so if two requests race (e.g. accept + decline) exactly one wins and the loser gets `409` — the database can never end in an invalid combination. No explicit `SELECT … FOR UPDATE` lock is needed.

### Send a request (`POST /competition-requests`)

Creates a `PENDING` request from the authenticated user to the given opponent. No competition row is created.

Flow:
1. Authenticate → `401` if missing/invalid/expired token; inactive challenger also `401`.
2. Pydantic validates the body → `422` on bad input (unrecognized/invalid UUID, or any duration outside `{30, 60, 360, 1440}`).
3. Opponent must exist → else `404` "User not found".
4. Opponent must be active → else `400` "Cannot send a request to an inactive user".
5. `opponent_id == current_user.user_id` → `400` "You cannot challenge yourself".
6. Insert the request (`challenger_id = current_user.user_id`, `status = PENDING`, `created_at = now()`).

Request body (`CompetitionRequestCreate`):

```json
{
  "opponent_id": "8bbf17f2-a7e1-…",
  "duration_minutes": 30
}
```

`duration_minutes` is a `Literal[30, 60, 360, 1440]` — exactly the four values allowed by the `competition_requests` `duration_minutes` check constraint (30/60/360/1440 minutes); any other value, including `0` or negatives, is rejected with `422`.

Response (`201 Created`) — the same `CompetitionRequestRead` shape used by every endpoint below:

```json
{
  "request_id": "c9a1f3e0-…",
  "challenger": {
    "user_id": "6a1c9f10-…",
    "username": "polar_bear",
    "profile_picture_url": "https://…/avatar.png",
    "biography": "I love cold snacks"
  },
  "opponent": {
    "user_id": "8bbf17f2-a7e1-…",
    "username": "gummy",
    "profile_picture_url": null,
    "biography": null
  },
  "duration_minutes": 30,
  "status": "PENDING",
  "created_at": "2026-08-22T09:15:00.123456",
  "responded_at": null
}
```

### List incoming requests (`GET /competition-requests/incoming`)

Requests addressed to the authenticated user (`opponent_id == current_user.user_id`). `limit` (default 20, 1–50) / `offset` (default 0) pagination, newest first (`ORDER BY created_at DESC`, tie-broken by `request_id`). Returns an array of the `CompetitionRequestRead` shape above — it includes every status (`PENDING`, `ACCEPTED`, `DECLINED`, `CANCELLED`), so the client can filter. A user can only ever see requests sent to themselves; no `user_id` input exists.

### List outgoing requests (`GET /competition-requests/outgoing`)

Requests sent by the authenticated user (`challenger_id == current_user.user_id`), same pagination/ordering/response shape. A user can only ever see requests they sent themselves.

### Accept a request (`POST /competition-requests/{request_id}/accept`)

Only the **opponent** can accept (anyone else → `403`). The request must still be `PENDING` (else `409`) and exist (else `404`). On success (Phase 6 Part 2) the request becomes `ACCEPTED` with `responded_at` set **and an `ACTIVE` `competitions` row is created from the same data — both in ONE database transaction** (see [Competitions (Phase 6, Part 2)](#competitions-phase-6-part-2)). The response body is unchanged: it returns the accepted request.

Response (`200 OK`): the `CompetitionRequestRead` shape with `"status": "ACCEPTED"` and a non-null `responded_at`.

### Decline a request (`POST /competition-requests/{request_id}/decline`)

Same rules as accept, but acting as the opponent returns `DECLINED`. Response (`200 OK`): the `CompetitionRequestRead` shape with `"status": "DECLINED"` and a non-null `responded_at`.

### Cancel a request (`POST /competition-requests/{request_id}/cancel`)

Same rules, but only the **challenger** may cancel their own outgoing request (else `403`). The request must be `PENDING` (else `409`) and exist (else `404`). Response (`200 OK`): the `CompetitionRequestRead` shape with `"status": "CANCELLED"` and a non-null `responded_at`.

### Error cases

| Case | Status | Detail |
| --- | --- | --- |
| Missing/invalid/expired token, or inactive account | `401` | — |
| Invalid body (bad UUID / disallowed duration / non-int) | `422` | — |
| Opponent does not exist | `404` | `User not found` |
| Opponent is inactive | `400` | `Cannot send a request to an inactive user` |
| Self-challenge | `400` | `You cannot challenge yourself` |
| Request does not exist | `404` | `Competition request not found` |
| Wrong participant (accept/decline) | `403` | `You are not the opponent of this request` |
| Wrong participant (cancel) | `403` | `You are not the challenger of this request` |
| Request already responded to (any terminal state, or a lost concurrency race) | `409` | `Competition request is no longer pending` |
| Either participant already has 3 active competitions (accept only) | `409` | `The active competition limit has been reached (maximum 3)` |
| The two users already have an active competition (accept only) | `409` | `These users already have an active competition` |
| A competition for this request already exists (legacy row, accept only) | `409` | `A competition for this request already exists` |

### Response safety

`CompetitionRequestRead` (`app/schemas/competition_request.py`) embeds the standard public user shape `UserPublic` for both participants — only `user_id`, `username`, `profile_picture_url`, `biography`. `email`, `password_hash`, `coin_balance`, `winning_streak`, `is_active`, `created_at` and other account fields never appear in list or detail responses (verified by both the schemas and the response whitelist).

### What this part deliberately does NOT do

- Does **not** move coins: `users.coin_balance` and the `coin_transactions` ledger are untouched — there are no coin operations in the request lifecycle itself.
- Does **not** implement voting, vote costs, prize-pool growth, winner calculation, rewards, competition completion, or (push) notifications — those are separate future Phases. (Competition **creation** on accept is Part 2 and is implemented; see the next section.)

No migration and no seed data were required: the `competition_requests` table and its status enum already existed in `310bd16d3308_initial_schema.py`, and the models already matched the database (`alembic check` reports no pending operations).

## Competitions (Phase 6, Parts 2–4)

Competition creation is not a standalone endpoint: it happens **inside** `POST /competition-requests/{request_id}/accept`, atomically with the request's transition to `ACCEPTED`. Accepting a valid PENDING request creates one `ACTIVE` `competitions` row that stays linked to its request through `competitions.request_id` (UNIQUE), copying the immutable terms from the request.

### Active-competition limits (business rule)

A user may participate in a **maximum of 3 ACTIVE competitions simultaneously**, and a given unordered pair of users may have **at most 1 ACTIVE competition** at a time (either direction). Only `status = 'active'` competitions count — **completed competitions do not count toward the limit**, and an old A-vs-B competition being completed is exactly what allows a new A-vs-B to start.

Enforcement happens on **acceptance, atomically with competition creation**:

```
BEGIN
  SELECT … FOR UPDATE on BOTH participant user rows   (serialize, see below)
  check challenger active-count < 3
  check opponent   active-count < 3
  check no existing active A-vs-B matchup (either direction)
  conditional request UPDATE -> ACCEPTED (WHERE status = 'PENDING')
  INSERT competition
COMMIT  — or ROLLBACK
```

- If **either participant already has 3 active competitions**, or the pair already **has an active competition**, the accept returns `409` and the whole transaction rolls back.
- The **request is left PENDING** — it is **not** auto-DECLINED or CANCELLED, so it can be accepted later (after an active competition completes or the matchup ends). No new request status was introduced.
- `409` details: `"The active competition limit has been reached (maximum 3)"` / `"These users already have an active competition"`.

Concurrency safety: the accept takes `SELECT … FOR UPDATE` row locks on **both** participant user rows (in sorted order, so the locking protocol is deadlock-free) **before** counting active competitions. Because every competition-creation transaction locks every user it touches, two simultaneous accepts involving the same user serialize on that user's row — a stale active-count read is impossible. (This is the `qr.py` "the lock, not the check, is what makes it safe" pattern.) A concurrency test drives a user at 2 active who accepts two requests at once and confirms exactly one succeeds — 3 active total, never 4.

### Atomically accepting + creating the competition

```
BEGIN
  conditional request UPDATE  -> status = ACCEPTED  (WHERE status = 'PENDING')   [only the opponent]
  INSERT competitions          -> request_id, challenger_id, opponent_id,
                                 duration_minutes copied from the request,
                                 status_id = 'active' (lookup, resolved by name),
                                 start_time = now (naive UTC, project convention)
COMMIT  — or ROLLBACK if the INSERT fails
```

What the database fills in for you: `end_time` is a Postgres **GENERATED** column (`start_time + duration_minutes * interval '1 minute'`), `prize_pool` defaults to **0**, `total_votes` defaults to **0**, `winner_id` is `NULL`, and `created_at` is `now()`.

Atomicity guarantees:

- The request can never be left `ACCEPTED` without its competition, and a competition can never exist while its request is still `PENDING`. If the `competitions` INSERT fails, the whole transaction rolls back — the request stays `PENDING`.
- **One competition per request** is guaranteed twice over: the `competitions.request_id` UNIQUE constraint at the database level, and the conditional `status = 'PENDING'` UPDATE at the application level. Of two concurrent accept attempts, only one wins the conditional UPDATE and only that transaction ever runs the competition INSERT; the loser gets `409` and creates nothing (verified by the concurrency test).

### Retrieve a competition (`GET /competitions/{competition_id}`)

A competition is **private to its two participants** at this stage — the challenger and the opponent. Requires `Authorization: Bearer <token>`; missing/invalid/expired token → `401`.

- A participant (challenger **or** opponent) → `200` with the `CompetitionRead` shape below.
- Any other authenticated user → `403` "You are not a participant of this competition".
- Unknown `competition_id` → `404` "Competition not found".
- To find competitions without knowing an id up front, use `GET /competitions` (below).

Request body: none.

Response (`200 OK`) — the exact `CompetitionRead` shape:

```json
{
  "competition_id": "3f0a1c55-…",
  "request_id": "c9a1f3e0-…",
  "challenger": {
    "user_id": "6a1c9f10-…",
    "username": "polar_bear",
    "profile_picture_url": "https://…/avatar.png",
    "biography": "I love cold snacks"
  },
  "opponent": {
    "user_id": "8bbf17f2-…",
    "username": "gummy",
    "profile_picture_url": null,
    "biography": null
  },
  "status": "active",
  "prize_pool": 0,
  "total_votes": 0,
  "winner_id": null,
  "duration_minutes": 60,
  "start_time": "2026-08-22T09:15:00.123456",
  "end_time": "2026-08-22T10:15:00.123456",
  "created_at": "2026-08-22T09:15:00.123456"
}
```

Important response fields: `status` is the competition_status lookup row's `status_name` (`"active"`, later `"completed"`); `winner_id` is exposed only when a winner exists (null until completion is implemented); both participants are the four-field `UserPublic` shape — never `email`/`password_hash`/`coin_balance`/`winning_streak`/account fields.

### List my competitions (`GET /competitions`)

Discovery for the mobile client — returns the authenticated user's competitions **only** (where `current_user` is the challenger **or** the opponent). Requires `Authorization: Bearer <token>`; missing/invalid/expired token → `401`. A user can never see competitions they are not part of (the filter is `WHERE challenger_id = me OR opponent_id = me`, and the user id comes only from the JWT).

Query parameters:

| Param | Rules |
| --- | --- |
| `status` | Optional. `active` or `completed`. Returns only competitions in that state. Missing/invalid values (`Literal`) → `422`. |
| `limit` | Optional, default `20`, min `1`, max `50`. Out of range → `422`. |
| `offset` | Optional, default `0`, min `0`. Out of range → `422`. |

Request body: none.

Ordering is deterministic:
- `status=active` → `end_time ASC` (the competition ending soonest first), tie-broken by `competition_id`.
- `status=completed` → `end_time DESC` (most recently ended first).
- no `status` → `created_at DESC`, `competition_id DESC` (newest-created first).

Response (`200 OK`) — an array of the same `CompetitionRead` shape (full example above), one element per competition:

```json
[
  {
    "competition_id": "3f0a1c55-…",
    "request_id": "c9a1f3e0-…",
    "challenger": { "user_id": "6a1c9f10-…", "username": "polar_bear", "profile_picture_url": "…", "biography": "…" },
    "opponent": { "user_id": "8bbf17f2-…", "username": "gummy", "profile_picture_url": null, "biography": null },
    "status": "active",
    "prize_pool": 0,
    "total_votes": 0,
    "winner_id": null,
    "duration_minutes": 60,
    "start_time": "2026-08-22T09:15:00.123456",
    "end_time": "2026-08-22T10:15:00.123456",
    "created_at": "2026-08-22T09:15:00.123456"
  }
]
```

### Discover public competitions (`GET /competitions/discover`)

The community feed for voting (Phase 6 Part 4A): returns **other users' ACTIVE competitions** so the authenticated caller can open — and, in Phase 4B, vote on — competitions without being a participant. Requires `Authorization: Bearer <token>`; missing/invalid/expired token → `401`.

What appears (all of these must hold):

- status = `active` only (completed competitions never appear).
- The caller is **not** the challenger **and** not the opponent — the caller's own competitions are excluded (a participant can never vote in their own competition).
- Both the challenger and the opponent are **active** accounts. A competition whose challenger or opponent has been deactivated is simply excluded from the feed (nothing is modified).

Query parameters:

| Param | Rules |
| --- | --- |
| `limit` | Optional, default `20`, min `1`, max `50`. Out of range → `422`. |
| `offset` | Optional, default `0`, min `0`. Out of range → `422`. |

Request body: none.

Ordering: deterministic — `end_time ASC` (the competition ending soonest first, most time-sensitive to vote on), tie-broken by `competition_id`.

Response (`200 OK`) — an envelope in the same style as the wardrobe/clothing feeds (`items`, `total`, `limit`, `offset`); each item is the safe `CompetitionRead` shape (full example above — for the feed the meaningful card fields are `competition_id`, `challenger`, `opponent`, `status`, `total_votes`, `start_time`, `end_time`):

```json
{
  "items": [
    {
      "competition_id": "3f0a1c55-…",
      "request_id": "c9a1f3e0-…",
      "challenger": { "user_id": "6a1c9f10-…", "username": "polar_bear", "profile_picture_url": "…", "biography": "…" },
      "opponent": { "user_id": "8bbf17f2-…", "username": "gummy", "profile_picture_url": null, "biography": null },
      "status": "active",
      "prize_pool": 0,
      "total_votes": 0,
      "winner_id": null,
      "duration_minutes": 60,
      "start_time": "2026-08-22T09:15:00.123456",
      "end_time": "2026-08-22T10:15:00.123456",
      "created_at": "2026-08-22T09:15:00.123456"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

Only the aggregate `total_votes` is exposed — no per-participant vote counts and no voter identities. Both participants are the four-field `UserPublic` shape; `email`, `password_hash`, `coin_balance`, `is_active`, auth-provider and device-token data never appear.

Errors: missing/invalid/expired token → `401`; invalid `limit`/`offset` → `422`. Unlike `GET /competitions` and `GET /competitions/{id}` (which are participant-scoped), discovery is a public, authenticated view of OTHER users' competitions. Voting is implemented (see below) — voting is currently **FREE**.

### Cast a vote (`POST /competitions/{competition_id}/votes`)

Lets the authenticated user vote in an **ACTIVE** competition they are **not** a participant of. Response is a confirmation with the fresh `total_votes`. Requires `Authorization: Bearer <token>`; missing/invalid/expired token → `401`.

**Current version: voting itself is implemented, but voting is currently FREE.** No coins are deducted, no `coin_transactions` row is written, and `users.coin_balance` is never touched. The next feature (Part 4B-2) will introduce **1 vote = 1 coin**.

Request body (`VoteCreate`):

```json
{
  "voted_for_user_id": "6a1c9f10-…"
}
```

Rules (all enforced from the JWT identity, never the frontend):

- The competition must exist → else `404` "Competition not found".
- It must be `ACTIVE` — voting in a completed competition → `409` "Competition is no longer active".
- **Self-voting is impossible**: a participant (challenger **or** opponent) → `400` "You cannot vote in your own competition". A can never vote for B in the A-vs-B competition.
- `voted_for_user_id` must be one of the two participants (challenger or opponent) → else `400` "You can only vote for a participant of this competition".
- **One vote per user per competition**: enforced by `UNIQUE(competition_id, voter_id)` — a second `POST` from the same voter → `409` "You have already voted in this competition". Votes are immutable (no switching, no deleting — a second vote is simply rejected).
- Invalid `competition_id` or `voted_for_user_id` (bad UUID) → normal Pydantic `422`.

Atomicity & concurrency: the `votes` INSERT and the `total_votes` increment are one transaction — the count only moves if the vote row is created, and vice-versa. `total_votes` is bumped with a single atomic `UPDATE competitions SET total_votes = total_votes + 1` (never a Python read-modify-write), so simultaneous votes from different users each count exactly once; simultaneous duplicate votes from the same user are serialized by the UNIQUE constraint — exactly one succeeds, the loser rolls back and gets `409`. Vote target validity is additionally back-stopped by a `votes` trigger in the model (installed only on table creation; a fresh-database constraint violation surfaces as the same `400`).

Response (`201 Created`):

```json
{
  "vote_id": "9c447a2f-…",
  "competition_id": "3f0a1c55-…",
  "voter_id": "b5e2d461-…",
  "voted_for_user_id": "6a1c9f10-…",
  "created_at": "2026-08-22T12:00:00.123456",
  "total_votes": 4
}
```

The response exposes only ids, the timestamp, and the updated aggregate count — no password/account/coin data. Individual vote identities (who voted for whom) are never exposed by any endpoint.

### What this part deliberately does NOT do

- Voting costs **are not implemented**: a vote costs nothing — no coin deduction, no `coin_transactions`, `users.coin_balance` untouched. **Voting is currently FREE** (the 1-vote = 1-coin cost is Phase 4B-2).
- Does **not** grow `prize_pool`, compute `winner_id`, or distribute rewards — a vote only bumps `competitions.total_votes` and records `voted_for_user_id` in `votes`.
- Does **not** implement `completed` transitions or automatic competition completion (the DB `completed` status exists and is set by tests/admin tooling, but the app has no completion flow yet).
- Does **not** create any notifications or touch `device_tokens`.

No migration was required: the `competitions` and `competition_status` tables (with their `active`/`completed` seed rows) already existed in `310bd16d3308_initial_schema.py`, and the models already matched the database (`alembic check` reports no pending operations).

## Security notes

- Passwords are hashed with **Argon2id** via `pwdlib` (`app/core/security.py`), using a reusable `PasswordHasher` so routes never contain hashing logic.
- Only the hash is stored in `users.password_hash`.
- Hashes are never returned by the API (`password_hash` is not a field in `UserRead`).
- Passwords are never logged.
- The JWT secret lives only in `.env` (gitignored) — it is not hardcoded in source.
- JWTs contain only `sub`/`iat`/`exp`; no password, hash, or sensitive profile data.
- The same `401` message covers wrong password and unknown user (no account enumeration).

## Tests

Hand-written scripts (no framework) that exercise the real HTTP + DB stack. Run them from the `backend/` directory:

```bash
# DB connectivity
venv/Scripts/python -m tests.test_db

# Registration end-to-end (creates unique test users, then deletes them;
# also verifies the avatar lifecycle: exactly one avatar per new user,
# linked to the user, with server-generated id and created_at)
venv/Scripts/python -m tests.test_register

# Login + JWT end-to-end (register -> login -> /users/me, plus 401 cases)
venv/Scripts/python -m tests.test_auth_flow

# PATCH /users/me end-to-end (single/multi-field update, duplicates, 401s, 422s)
venv/Scripts/python -m tests.test_update_profile

# GET /users/search end-to-end (partial/case-insensitive match, 401s, 422s,
# self-exclusion, inactive users, public-field-only responses)
venv/Scripts/python -m tests.test_user_search

# Follow/unfollow end-to-end (valid follow, duplicates, self-follow, 404s,
# 400s, 401s, follow-status, body-supplied follower_id ignored)
venv/Scripts/python -m tests.test_follow

# QR redemption end-to-end (valid/redeemed/expired/overdue codes, 404s,
# 401s, response field whitelist, DB row updated, coins awarded + ledger row)
venv/Scripts/python -m tests.test_qr_redeem

# QR redemption coin award (deep: correct coins, ledger row, balance_after,
# qr_id reference, no double award, failed-transaction rollback, concurrency)
venv/Scripts/python -m tests.test_qr_redeem_coins

# Coin balance + history end-to-end (balance matches DB, ordering,
# pagination, isolation between users, empty history, 401s, 422s,
# field whitelist, QR/product reference surfaced from a real redemption)
venv/Scripts/python -m tests.test_user_coins

# Admin panel end-to-end (admin auth 401/403/granted, QR list/create/detail/
# status management, invalid value/product/transition errors, product list)
venv/Scripts/python -m tests.test_admin_qr

# Admin user management end-to-end (401/403/granted, list ordering/pagination,
# username+email search, is_active filter, detail 404s, deactivate/reactivate
# incl. login enforcement + self-deactivation guard, no password leakage)
venv/Scripts/python -m tests.test_admin_users

# Clothing browse end-to-end (401s, 200s, AVAILABLE-only rule, category
# filter/404/422s, deterministic ordering, pagination, response whitelist)
venv/Scripts/python -m tests.test_clothing_browse

# Clothing purchase end-to-end (401s, successful purchase with wardrobe +
# ledger + balance checks, insufficient balance, unavailable/upcoming,
# nonexistent item, duplicate purchase, mid-transaction rollback, DB-layer
# and HTTP-layer concurrency races)
venv/Scripts/python -m tests.test_clothing_purchase

# Admin clothing management end-to-end (401/403/granted on every endpoint,
# categories lookup, create/list/detail/update/delete, search + category +
# availability filters, public browse/purchase compatibility, delete safety)
venv/Scripts/python -m tests.test_admin_clothing

# Wardrobe listing end-to-end (401s, empty wardrobe, real purchase surfaced,
# newest-first ordering, limit/offset pagination + 422s, user isolation
# between two users, owned UNAVAILABLE/UPCOMING items stay visible,
# response field whitelists with no sensitive data)
venv/Scripts/python -m tests.test_wardrobe

# Equip/unequip end-to-end (401s, legacy missing-avatar 404, successful equip with
# DB row checks, ownership isolation both directions, slot determination for
# all six categories, replacement within a slot, multi-slot coexistence,
# unequip + incorrect-unequip 409, availability independence, coin safety,
# concurrent equips into one slot)
venv/Scripts/python -m tests.test_wardrobe_equip

# Avatar retrieval end-to-end (registration-created avatars incl. the
# one-avatar-per-user UNIQUE guarantee, 401s, legacy missing-avatar 404,
# empty avatar with all six slots null, one/multiple equipped items in
# their own slots, replacement within a slot, user isolation both
# directions + smuggled query params ignored, UNAVAILABLE equipped item
# stays visible, response field whitelists with no sensitive data)
venv/Scripts/python -m tests.test_avatar

# Competition requests end-to-end (401s, successful PENDING send, challenger
# taken from the JWT not the body, self-challenge 400, missing opponent 404,
# inactive opponent 400, invalid durations 422, all four valid durations,
# duplicate PENDING requests allowed, no sensitive user data, incoming/
# outgoing isolation, accept/decline/cancel role rules 403, terminal-state
# retries 409, responded_at populated on transition, outsider isolation)
venv/Scripts/python -m tests.test_competition_requests

# Competition creation & lifecycle end-to-end (accept atomically creates one
# ACTIVE competition, request/parties/duration copied, status active,
# total_votes 0, winner NULL, start_time set, end_time = start_time + duration,
# prize_pool 0, atomicity rollback on a duplicate-competition INSERT failure,
# re-accept 409, concurrent accepts -> one 200 + one 409 + one competition,
# accept authorization 403s create nothing, participant-only retrieval with a
# safe whitelisted payload, unauthenticated 401)
venv/Scripts/python -m tests.test_competition_creation

# Competition management & discovery end-to-end (max 3 ACTIVE per user: 0/1/2
# active can accept, 3 cannot, challenger-at-3 and opponent-at-3 both block
# with 409 + request left PENDING + no competition; completed competitions
# excluded from the limit; duplicate active matchup blocked both directions,
# recompete allowed after completion; GET /competitions participant-scoped
# with status filter active/completed + 422 on invalid + 401 unauth; detail
# remains participant-only; concurrent accepts at 2 active -> exactly 3 total)
venv/Scripts/python -m tests.test_competition_management

# Public competition discovery end-to-end (401 unauth; active competitions
# between two OTHER active users appear with competition_id; own competitions
# as challenger AND as opponent excluded; completed excluded; inactive
# challenger/opponent excluded; UserPublic-only payload with no sensitive
# fields; default limit 20, max 50 enforced, invalid limit/offset -> 422;
# limit/offset pages tile the whole feed without overlap; deterministic
# end_time ASC ordering, shorter-duration competition first)
venv/Scripts/python -m tests.test_competition_discover

# Basic vote casting end-to-end (third-party users vote for challenger AND
# opponent -> 201 with confirmation + fresh total_votes; exactly one vote
# record; total_votes +1 per vote; unauthenticated 401; challenger/opponent
# self-vote -> 400 with no row; non-participant target -> 400; duplicate vote
# -> 409 with count unchanged; concurrent duplicate votes -> one 201/one 409,
# one row, +1 only; completed competition -> 409; voting is FREE — no
# coin_balance change and no coin_transactions row)
venv/Scripts/python -m tests.test_vote_casting
```

`test_register.py` currently verifies: successful registration, password stored as an Argon2 hash (not plain text), registration creating exactly one avatar for the new user (linked to the user, with server-generated id and created_at — the one-avatar-per-user invariant), duplicate username rejected, duplicate email rejected (case-insensitively), invalid payloads rejected by Pydantic, and no password/hash leakage in responses. All test users are cleaned up afterward.

`test_auth_flow.py` verifies: login returns a token, the JWT contains only `sub`/`iat`/`exp` with no sensitive data, `GET /users/me` works with a valid token, and returns `401` for wrong password, nonexistent user, missing/invalid/expired token, and inactive users. All test users are cleaned up afterward.

`test_update_profile.py` verifies: updating one field leaves the others unchanged (username/biography/profile updates all persisted to the DB), clearing a field with `null`, duplicate username → `409` (including the case-insensitive `CITEXT` case), reusing your own username allowed, missing token → `401`, empty/invalid/`null` username → `422`, and no `password_hash` in the response. All test users are cleaned up afterward.

`test_user_search.py` verifies: partial (prefix and mid-string) matches, case-insensitive matching, no matches → `[]`, the caller excluded from their own results, missing/invalid/expired token → `401`, missing/empty/too-long `q` → `422`, `limit` bounds → `422`, inactive users excluded, `%`-wildcards treated literally, and responses exposing only the four public fields. All test users are cleaned up afterward.

`test_follow.py` verifies: missing/invalid/expired token → `401`, follow nonexistent user → `404`, follow inactive user → `400`, follow yourself → `400`, valid follow → `201` with the row actually in the DB, duplicate follow → `409` with no duplicate row, a body-supplied `follower_id` is ignored (the follower always comes from the JWT), follow-status returns `is_following: true`/`false`, unfollow someone not followed → `404`, valid unfollow → `200` with the row actually deleted, unfollow again → `404`, and a second user can follow the same target. All test users (and their follow rows, via cascade) are cleaned up afterward.

`test_qr_redeem.py` verifies: missing/invalid/expired token → `401`, nonexistent code → `404`, already-redeemed code → `409`, expired code → `410`, active-but-past-`expires_at` code → `410`, valid active code → `200` with `message`/`coins_earned`/`balance` **and** the row actually updated in the DB (status `REDEEMED`, `redeemed_by_user_id` = caller, `redeemed_at` set), the user's `coin_balance` increased by `coin_value` with exactly one `coin_transactions` row, the response exposes **only** those three safe fields, and the same code can't be redeemed again by the same or a different user → `409` with the original owner preserved. Test products, qr_codes, users, and their coin transactions are cleaned up afterward.

`test_qr_redeem_coins.py` verifies the coin award in depth: a valid redemption credits exactly `coin_value` coins and reports the correct new balance; the QR row becomes `REDEEMED`; exactly one `coin_transactions` row is created with the right `amount`, `balance_after`, `user_id`, `type_id` (`qr_redemption`), and `qr_id`; a second redemption correctly stacks onto the first (`balance_after` is the running total); redeeming the same code again → `409` with no second credit and still one ledger row; a mid-transaction failure (the `qr_redemption` type temporarily made unresolvable) rolls back the claim, the credit, and the ledger; and two concurrent attempts on the same code — both through two real DB connections and through concurrent HTTP calls — result in exactly one success and one `409`, with the coins awarded exactly once. All test data is cleaned up afterward.

`test_user_coins.py` verifies the read side: missing/invalid/expired token → `401` on both endpoints; `GET /users/me/coins` returns the exact `coin_balance` from PostgreSQL; a client-supplied `user_id` query param is ignored; `GET /users/me/transactions` returns all of the user's rows in newest-first order with every row exposing only the whitelisted fields (`transaction_id`, `amount`, `balance_after`, `transaction_type`, `direction`, `created_at`, `qr`, and the nullable `competition_id`/`wardrobe_id`/`vote_id`); a real QR redemption surfaces with its QR code and product name; debit rows serialize as `direction: DEBIT` with negative amounts; `limit`/`offset` pagination tiles the full history with no overlap and empties beyond the end; another user's transactions are invisible (user B sees only B's rows) and balances are separate; empty history → `[]`; and invalid `limit`/`offset` (0, 51, -1, non-numeric, negative offset) → `422`. All test data is cleaned up afterward.

`test_admin_qr.py` verifies the internal admin console: unauthenticated → `401` and normal users → `403` on every `/admin/*` endpoint; an administrator is granted access; `GET /admin/qr-codes` paginates newest-first with `total`/`limit`/`offset` and supports `status`/`product_id` filters; `GET /admin/qr-codes/{qr_id}` returns the product and (for redeemed codes) who redeemed it and when; `POST /admin/qr-codes` returns `201` with a generated unique `PB-` code that appears in the list, rejects a nonexistent product with `404` and non-positive coin values with `422`; `PATCH /admin/qr-codes/{qr_id}` allows ACTIVE ⇄ EXPIRED (deactivate/reactivate) but rejects ACTIVE → REDEEMED and any change to a REDEEMED code with `409` (audit trail), returning `404` for nonexistent codes; and a normal user's attempts — list, create, status change — are all denied with `403` and the rows are left untouched. The product list used by the create form is now served by the dedicated `GET /admin/products` module and is covered there. All test data is cleaned up afterward.

`test_admin_products.py` verifies the product-management module: unauthenticated → `401` and normal users → `403` on every `/admin/products` endpoint (list, detail, create, update, delete) with an administrator granted; `GET /admin/products` paginates newest-first with `total`/`limit`/`offset`, supports case-insensitive, ILIKE-escaped name/SKU search, and exposes only `product_id`/`name`/`sku`/`created_at`/`qr_code_count`; `GET /admin/products/{product_id}` returns the full safe view (with `qr_code_count`) and `404` for a nonexistent product; `POST` returns `201` with a database-generated `product_id`/`created_at`, trims whitespace, rejects a duplicate SKU with `409` and blank fields with `422`, and ignores client-supplied id/timestamp; `PATCH` updates name and/or SKU, cannot change `product_id`/`created_at`, rejects a SKU already in use by another product with `409`, and returns `404`/`422` as appropriate; `DELETE` removes an unreferenced product (`204`) but refuses a product referenced by QR codes with `409`, leaving the row and its audit history untouched; and a QR created against a product is reflected in `qr_code_count` and blocks deletion. All test data is cleaned up afterward.

`test_clothing_browse.py` verifies the shop browsing surface: missing/invalid/expired token → `401` on `GET /clothing` and a valid token → `200`; unfiltered listing returns only the whitelisted item fields (`item_id`, `name`, `description`, `category` → `category_id`/`category_name`/`slot`, `price`, `image_url`, `availability_status`, `collection_id`); ordering is deterministic (`created_at DESC`, `item_id DESC` tiebreak) — a repeated request is identical and matches a direct DB query; `limit`/`offset` pagination tiles the result set with no overlap and empties past the end with a correct `total`; filtering by a real category id returns only that category's items; a category with no AVAILABLE items returns `total: 0`; the AVAILABLE-only rule hides `UNAVAILABLE` and `UPCOMING` items; a nonexistent in-range `category_id` → `404`; non-integer/out-of-range/zero/negative `category_id` and invalid `limit`/`offset` → `422`; `collection_id` round-trips when set and is `null` otherwise; and `availability_status` serializes to `"available"`. The test creates its own categories/items (RUN_ID-isolated) and cleans everything up afterward.

`test_clothing_purchase.py` verifies the purchase flow end-to-end: missing/invalid/expired token → `401`; a valid purchase → `200` with exactly the whitelisted response fields (`message`, `wardrobe_id`, `item` in the catalog shape, `amount_spent`, `remaining_balance`, `transaction_id`) and the row-level truth behind each field (a real `user_wardrobe` row, `coin_balance` debited by exactly the catalog price, one `coin_transactions` row with negative amount, type `clothing_purchase`, correct `balance_after`, correct `wardrobe_id` reference, `created_at` set); insufficient balance (including zero) → `400` with no wardrobe row, no ledger row, unchanged balance; `UNAVAILABLE` and `UPCOMING` items → `409` with no changes; nonexistent item → `404` and malformed uuid → `422` with no changes; buying the same item twice → first `200`, second `409`, charged once, single wardrobe + ledger row; a mid-transaction failure (the `clothing_purchase` lookup row temporarily renamed) rolls back the debit, the wardrobe insert, and the ledger together, then the lookup row is restored; two concurrent DB-layer purchases of two different 100-coin items with only 100 coins → exactly one `200`/one `400`, balance 0, never negative, one wardrobe + one ledger row; two concurrent DB-layer purchases of the same item → one `200`/one `409`, charged once; and the same guarantee through concurrent HTTP requests. The test creates its own category/items (RUN_ID-isolated) and cleans everything up afterward.

`test_admin_clothing.py` verifies the clothing-management module: unauthenticated → `401` and normal users → `403` on every `/admin/clothing*` endpoint (list, categories, detail, create, update, delete) with an administrator granted; `GET /admin/clothing/categories` returns all seeded lookup rows (id/name/slot) ordered by id; `POST` returns `201` with database-generated `item_id`/`created_at`, defaults availability to `available`, trims whitespace, accepts `price = 0` but rejects negatives/non-integers with `422`, rejects unknown categories with `404`, blank names/image URLs with `422`, non-enum availabilities with `422`, and ignores client-supplied id/timestamp; `GET /admin/clothing` paginates newest-first (matching a direct DB query), tiles pages without overlap, searches name AND description case-insensitively with ILIKE escaping, filters by category (`404` on unknown) and by each availability value — including `UNAVAILABLE`/`UPCOMING`, which admins see although public browse hides them — and exposes exactly the whitelisted fields; `GET /{item_id}` returns the full view and `404` for a nonexistent item; `PATCH` updates every catalog field independently (including clearing description/collection with `null` and moving an item to another category, inheriting its slot), revalidates the category, keeps `item_id`/`created_at` immutable, and returns `404`/`422` as appropriate; public compatibility: an admin-created `AVAILABLE` item appears in `GET /clothing` and is purchasable, marking it `UNAVAILABLE` removes it from browse and makes purchase return `409`; delete safety: a wardrobe-referenced item → `409` with both the item and the ownership record intact, an unreferenced item → `204` and gone, nonexistent → `404`. All test data is cleaned up afterward.

`test_wardrobe.py` verifies the wardrobe listing: missing/invalid/expired token → `401` and a valid token → `200`; a user who never purchased gets `200` with `"items": []` and `"total": 0`; one real purchase surfaces as exactly one entry whose `wardrobe_id`, `purchased_at`, and catalog-shaped `item` match the purchase response and the DB row; multiple entries come back newest-purchase-first (`purchased_at DESC`, `wardrobe_id DESC` tiebreak, verified against controlled timestamps) with repeated requests returning an identical order; `limit`/`offset` pagination tiles the wardrobe without overlap or gaps, empties past the end with `total` intact, accepts the maximum limit of 100, and rejects invalid values with `422`; user isolation — two users with disjoint wardrobes each see only their own items, and a smuggled `?user_id=…` query parameter is ignored entirely; availability independence — owned items flipped to `UNAVAILABLE`/`UPCOMING` remain visible in the wardrobe; and data safety — the envelope exposes only `items`/`total`/`limit`/`offset`, each entry only `wardrobe_id`/`purchased_at`/`item`, the nested item matches the public catalog shape, and no username/email/password/auth-provider/balance/admin fields appear anywhere in the payload. All test users, items, and categories are cleaned up afterward.

`test_wardrobe_equip.py` verifies equip/unequip end-to-end: missing/invalid/expired token → `401` on both endpoints; a registered user WITHOUT an avatar (registration does not create one) gets an explicit `404` "Avatar not found" instead of a silently created avatar; a valid equip → `200` with exactly the whitelisted fields (`message`, `equipment` → `avatar_id`/`slot`/`equipped_at`/catalog-shaped `item`) and exactly one `avatar_equipment` row with the caller's avatar, the category-derived slot, the owned item, and a server-written `equipped_at`; ownership isolation — user A equipping or unequipping user B's `wardrobe_id` → `404` with neither user's equipment changed; slot determination — items from all six categories (hair/hat/top/bottom/shoes/accessory) always land in their category's slot; replacement — equipping Shirt B over Shirt A in TOP leaves exactly one equipment row (Shirt B) while Shirt A stays owned in `user_wardrobe`; multi-slot coexistence — TOP + BOTTOM + SHOES (+ others) coexist on one avatar; unequip removes only the equipment row and keeps ownership; incorrect unequip — with Shirt A equipped, unequipping Shirt B → `409` "This item is not currently equipped" and Shirt A stays equipped (same for an owned-but-unequipped item); availability independence — an equipped item marked `UNAVAILABLE` stays equipped and can still be unequipped; coin safety — `coin_balance` unchanged and zero new `coin_transactions` rows after all equip/unequip traffic; nonexistent `wardrobe_id` → `404` and malformed uuid → `422` on both endpoints; no sensitive fields anywhere in payloads; and two concurrent HTTP equips of different shirts into TOP end with exactly ONE valid equipment row. All test users, avatars, items, and categories are cleaned up afterward.

`test_avatar.py` verifies the avatar retrieval endpoint: missing/invalid/expired token → `401`; a registered user WITHOUT an avatar gets an explicit `404` "Avatar not found" (no silent creation in a read endpoint); an avatar with nothing equipped → `200` with the correct `avatar_id` and ALL SIX slot keys present and explicitly null; one equipped TOP item (placed through the real wardrobe equip endpoint) appears only in `top` with the correct `item_id`/name/image URL/category slot/server-written `equipped_at` while every other slot stays null; items equipped into all six slots each appear in THEIR OWN slot and never in another's; replacement — after equipping Shirt B over Shirt A in TOP, the payload reports Shirt B and Shirt A's id appears nowhere; user isolation — users A and B with differently equipped avatars each see ONLY their own `avatar_id` and equipment in both directions, and smuggled `?user_id=`/`?avatar_id=` query parameters are ignored entirely; availability independence — an equipped item an admin marks `UNAVAILABLE` stays visible on the avatar with its current status; data safety — the envelope exposes exactly `avatar_id`/`equipment`, the map exactly the six slot keys, occupied slots exactly `equipped_at`/`item`, items match the public catalog shape, and no password/email/auth-provider/balance/streak/biography/username fragments appear anywhere in the payload. Equipment is placed exclusively through the wardrobe equip endpoint so Phase 4 → Phase 5 integration is exercised end to end. All test users, avatars, items, and categories are cleaned up afterward.

`test_competition_requests.py` verifies the competition-request lifecycle (Phase 6, Part 1): unauthenticated → `401` on all six endpoints; a successful send → `201` with `status: "PENDING"`, correct parties, `duration_minutes`, and `responded_at: null`; a body-supplied `challenger_id`/`status` is ignored — the challenger always comes from the JWT; self-challenge → `400`; nonexistent opponent → `404`; inactive opponent → `400` (from any sender); invalid durations (`15`, `120`, `0`, `-30`) → `422` while all four valid durations (`30`/`60`/`360`/`1440`) → `201`; duplicate PENDING requests to the same opponent are allowed (the documented design), each with a distinct `request_id`; responses expose only `request_id`/`challenger`/`opponent`/`duration_minutes`/`status`/`created_at`/`responded_at` where both users are the four-field `UserPublic` shape (no email/password/coin/streak leakage); incoming/outgoing lists are strictly isolated (B sees requests addressed to B, C sees none of A/B's); accept/decline by the opponent and cancel by the challenger → `200` with the terminal status and a populated `responded_at` (verified in both the response and the DB); the wrong party (challenger accepts/declines, opponent cancels, non-participant anything) → `403` with the request left untouched; non-pending requests (ACCEPTED/DECLINED/CANCELLED) reject accept/decline/cancel with `409`, including all invalid cross-transitions; a nonexistent `request_id` → `404`; and an outsider's attempts to accept/decline/cancel another user's request all fail `403` with the state unchanged. All test users and their requests (removed first because the user FKs are `RESTRICT`) are cleaned up afterward.

`test_competition_creation.py` verifies competition creation & lifecycle (Phase 6, Part 2): accepting a valid PENDING request returns `200`, transitions the request to `ACCEPTED`, and creates **exactly one** `ACTIVE` competition whose `request_id`/`challenger_id`/`opponent_id`/`duration_minutes` are copied from the request; `status` resolves to the `active` lookup row, `total_votes` = 0, `winner_id` = NULL, `prize_pool` = 0 (DB server default), `start_time` populated, and `end_time` (a DB GENERATED column) = `start_time` + duration in wall-clock minutes; `GET /competitions/{competition_id}` returns `200` with the full line of safe fields to **either** participant (challenger A and opponent B), `403` to a non-participant, `401` unauthenticated, and never leaks email/password/coin/streak data; re-accepting an already-ACCEPTED request → `409` with still exactly one competition ("same request cannot produce two competitions"); atomicity — when the competition INSERT is forced to fail (a pre-existing competition row for the same request), the accept returns `409`, the request rolls back to `PENDING`, and only the pre-existing competition survives; a challenger or an unrelated user accepting → `403` and creates no competition, the request left `PENDING`; and two concurrent HTTP accepts of the same PENDING request → exactly one `200` and one `409`, exactly one competition row, request `ACCEPTED`. All test rows (competitions → requests → users, in FK-correct order) are cleaned up afterward.

`test_competition_management.py` verifies competition management & discovery (Phase 6, Part 3): a user with 0/1/2 active competitions can accept (reaching 3), but once at 3 active they cannot accept another — both "opponent at 3" and "challenger at 3" return `409`, leave the request `PENDING`, and create no competition; the duplicate active-matchup rule blocks a second A-vs-B in either direction (`409`, request `PENDING`, no row) while an A-vs-B **recompete succeeds after completion**; completed competitions are excluded from the 3-active count (after completing one, the user at 2 active accepts the same opponent again → `200` at 3 active); `GET /competitions` is participant-scoped (A sees only A's competitions, D never sees the A-vs-B one), `?status=active` returns only active with `end_time ASC` (soonest-ending first), `?status=completed` only completed, an invalid status → `422`, and unauthenticated → `401`; `GET /competitions/{id}` stays participant-only (`200` for challenger/opponent, `403` for a third user, `401` unauthenticated); and concurrency — a user at 2 active who accepts two pending requests simultaneously ends with **exactly 3 active** competitions: one `200` + one `409`, one new competition, one request `ACCEPTED` and the other left `PENDING` (serialized by the `FOR UPDATE` user-row locks). All test rows are cleaned up afterward.

`test_competition_discover.py` verifies public competition discovery (Phase 6, Part 4A): unauthenticated `GET /competitions/discover` → `401`; viewer C discovers **active** competitions between **other active users** (A-vs-B and A-vs-D appear with their `competition_id`); C's own competitions — as challenger (C-vs-D) **and** as opponent (E-vs-C) — are excluded; a completed competition (E-vs-F) is excluded; a competition whose challenger is deactivated (X-vs-B) and one whose opponent is deactivated (A-vs-Y) are both excluded; every feed item has `status: "active"`, `total_votes: 0`, and the caller as a non-participant; the payload is the `{items, total, limit, offset}` envelope where every user is the four-field `UserPublic` shape with no `password_hash`/`email`/`coin_balance`/`is_active` anywhere; default `limit` is `20`, `limit=50` is accepted, `limit=51`/`limit=0`/negative `offset` → `422`; `limit`/`offset` pages tile the entire feed with no overlap or gaps; and ordering is deterministic `end_time ASC` — the shorter-duration competition appears before the longer one (ending soonest first). All test rows are cleaned up afterward.

`test_vote_casting.py` verifies basic vote casting (Phase 6, Part 4B-1): a third-party voter can vote for the **challenger** (`201` with `vote_id`/`created_at`/`total_votes`) and another for the **opponent** (`201`, `total_votes = 2`); each successful vote creates exactly one `votes` row and bumps stored `total_votes` by exactly 1 (confirmed in the DB); unauthenticated → `401`; the challenger and the opponent voting in their own competition → `400` with no vote row; voting for a non-participant → `400`; a duplicate vote from the same voter → `409` "You have already voted in this competition" with no new row and `total_votes` unchanged (the `UNIQUE(competition_id, voter_id)` constraint is the enforcing layer); two concurrent duplicate votes from one voter → exactly one `201` and one `409`, one vote row, `total_votes` +1 only (atomic `total_votes = total_votes + 1` UPDATE, loser rolled back); voting in a **completed** competition → `409` with no row and the count unchanged; and **coin safety** — after all the voting, every user's `coin_balance` is still `0` and zero `coin_transactions` rows exist (voting is FREE in this stage; the 1-vote = 1-coin cost is Phase 4B-2). All test rows (votes → competitions → requests → users) are cleaned up afterward.

## Seed data

Everything below is reference/development data inserted by Alembic migrations (`op.bulk_insert`, applied by `alembic upgrade head`) — it is **not** created by the application. Database-generated ids (`category_id`/`type_id`/`status_id`) below therefore depend on the seed order; the seeds resolve category ids by name, not by hard-coded number.

### Clothing categories (from `310bd16d3308`)

| `category_id` | `category_name` | `slot` |
| --- | --- | --- |
| 1 | Hairstyles | hair |
| 2 | Hats | hat |
| 3 | Tops | top |
| 4 | Bottoms | bottom |
| 5 | Sneakers | shoes |
| 6 | Sunglasses | accessory |

### Clothing items — the shop catalog (from `e833bac26dfc`)

18 items, 12 `AVAILABLE` / 3 `UNAVAILABLE` / 3 `UPCOMING` — enough to exercise normal browsing, category filtering, pagination, and price/availability variety. `item_id` and `created_at` are database-generated (`gen_random_uuid()` / `now()`); `collection_id` is `NULL` for all seeds.

| Name | Category | Price | Availability | Description |
| --- | --- | --- | --- | --- |
| Windblown Waves | Hairstyles | 300 | AVAILABLE | *(no description)* |
| Classic Crew Cut | Hairstyles | 250 | AVAILABLE | A timeless, low-maintenance trim. |
| Neon Faux Hawk | Hairstyles | 400 | UPCOMING | Summer drop — the most electric hair in town. |
| Polar Snapback | Hats | 350 | AVAILABLE | Flat brim, adjustable strap, ice-cold fit. |
| Sunrise Bucket Hat | Hats | 200 | AVAILABLE | Beach-ready and fully reversible. |
| Winter Toque Deluxe | Hats | 280 | UNAVAILABLE | Sold out for the season — cozy fleece lining. |
| Cola Classic Tee | Tops | 150 | AVAILABLE | The everyday classic, 100% cotton. |
| Polar Hoodie | Tops | 500 | AVAILABLE | Heavyweight fleece with a hidden pocket. |
| Limited Cold Brew Jacket | Tops | 900 | UPCOMING | Releasing soon — the collectors' piece. |
| Soda Pop Shorts | Bottoms | 180 | AVAILABLE | Lightweight summer shorts, two zip pockets. |
| Chill Cargo Pants | Bottoms | 420 | AVAILABLE | Six pockets, tapered fit, zero compromises. |
| Retro Racer Tracksuit Pants | Bottoms | 380 | UNAVAILABLE | Limited run from last season. |
| Fizzy Kicks | Sneakers | 600 | AVAILABLE | Bubble-soled sneakers with a carbon pop of colour. |
| Bubbles Running Shoes | Sneakers | 550 | AVAILABLE | Cushioned everyday runners. |
| Midnight Cola High-Tops | Sneakers | 750 | UPCOMING | Dropping soon — midnight gloss upper. |
| Polar Shades | Sunglasses | 120 | AVAILABLE | Classic aviator cut with UV400 protection. |
| Ice Cool Sunglasses | Sunglasses | 140 | AVAILABLE | Frost-tinted lenses for bright days. |
| Retro Cola Aviators | Sunglasses | 220 | UNAVAILABLE | Iconic gold frame from the archive vault. |

Each image URL is `https://mycolabear.example.com/clothing/<category>/<slug>.png` (placeholder host, not a real CDN).

### Coin transaction types (from `310bd16d3308`)

| `type_id` | `type_name` | `direction` |
| --- | --- | --- |
| 1 | qr_redemption | CREDIT |
| 2 | competition_reward | CREDIT |
| 3 | clothing_purchase | DEBIT |
| 4 | vote_cast | DEBIT |
| 5 | refund | CREDIT |
| 6 | admin_adjustment | CREDIT |

### Competition statuses (from `310bd16d3308`)

| `status_id` | `status_name` |
| --- | --- |
| 1 | active |
| 2 | completed |

### Notification types (from `310bd16d3308`)

| `type_id` | `type_name` |
| --- | --- |
| 1 | new_follower |
| 2 | competition_request |
| 3 | competition_accepted |
| 4 | competition_won |
| 5 | competition_lost |
| 6 | qr_redeemed |
| 7 | clothing_purchased |

## Admin panel

The internal administration console lives in two parts:

- **Backend** — every route under `/admin/` in `app/routers/admin/`, protected by `get_current_admin()` (`app/dependencies.py`). The chain is always `JWT → get_current_user() → 401 if bad → is_admin? → 403 if not → endpoint`, so authorization is enforced by the server, not the browser.
- **Frontend** — a small Vite + React app in `../admin-frontend/` (`npm run dev` on port 5173, proxying API calls to the backend on :8000).

### Granting an administrator

There is no "promote me" endpoint by design. Promote an operator directly in PostgreSQL (the app never sets `is_admin`):

```sql
UPDATE users SET is_admin = true WHERE username = '<your-admin-username>';
```

`users.is_admin` was added by the Alembic migration `7472ed35683a` (already applied when you ran `alembic upgrade head`).

### Admin API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/admin/me` | Current administrator identity (sidebar header / access check) |
| `GET` | `/admin/qr-codes` | Paginated QR list, newest first; `status` and `product_id` filters |
| `POST` | `/admin/qr-codes` | Generate a new active QR code (`product_id`, `coin_value`, optional `expires_at`) |
| `GET` | `/admin/qr-codes/{qr_id}` | Full administrative detail incl. redemptions |
| `PATCH` | `/admin/qr-codes/{qr_id}` | Deactivate (`active → expired`) or reactivate (`expired → active`) |
| `GET` | `/admin/products` | Paginated product list, newest first; `q` (name/SKU) search; each row has `qr_code_count` |
| `GET` | `/admin/products/{product_id}` | Full administrative detail incl. QR reference count |
| `POST` | `/admin/products` | Create a product (`name`, unique `sku`) — `201`, id/timestamp DB-generated |
| `PATCH` | `/admin/products/{product_id}` | Update `name` and/or `sku`; `product_id`/`created_at` are immutable |
| `DELETE` | `/admin/products/{product_id}` | Delete an unreferenced product; `409` if QR codes reference it |
| `GET` | `/admin/users` | Paginated user list, newest first; `q` (username/email) search and `is_active` filter |
| `GET` | `/admin/users/{user_id}` | Safe administrative detail for one user |
| `PATCH` | `/admin/users/{user_id}/status` | Deactivate (`{"is_active": false}`) or reactivate a user |
| `GET` | `/admin/clothing/categories` | The `clothing_categories` lookup rows (id/name/slot) for the admin form's category dropdown |
| `GET` | `/admin/clothing` | Paginated catalog list, newest first, **all** availability statuses; `q` (name/description) search, `category_id` and `availability` filters |
| `GET` | `/admin/clothing/{item_id}` | Full administrative detail for one item |
| `POST` | `/admin/clothing` | Create an item (`name`, `description`, `category_id`, `price`, `image_url`, optional `availability_status`/`collection_id`) — `201`; category must exist (`404`), price ≥ 0, availability defaults to `available` |
| `PATCH` | `/admin/clothing/{item_id}` | Update any catalog field (single-field PATCH); revalidates the category; `item_id`/`created_at` are immutable |
| `DELETE` | `/admin/clothing/{item_id}` | Delete an **unreferenced** item; `409` if any user owns/wears it — mark it `UNAVAILABLE` instead |

Status rules are enforced on the server: only `ACTIVE ⇄ EXPIRED` transitions are allowed; `REDEEMED` codes are immutable (they are audit history) and `ACTIVE → REDEEMED` is rejected with `409`. Users are never deleted — toggling `is_active` is the only administrative mutation, and an administrator cannot deactivate their own account (`400`). Products are edited in place (`name`/`sku`); `product_id` and `created_at` are database-owned, and a product referenced by QR codes cannot be deleted (the `RESTRICT` FK would break audit history, so the API returns `409`).

Clothing is managed on the **same** `clothing_items` table the public shop browses/purchases from — there is no second catalog. The slot is never client-supplied: it is inherited from the selected `clothing_categories` row. Deletion mirrors the products rule but stricter: `user_wardrobe.item_id` and `avatar_equipment.item_id` are both `RESTRICT` FKs and ownership/equipment rows are historical records, so an item with any wardrobe or equipment reference returns `409` ("…Mark it UNAVAILABLE instead."). Removing an item from the shop is done by editing its availability to `unavailable`, which hides it from browsing and blocks purchases while preserving every user's ownership history. No soft-delete column was needed — the existing `availability_status` provides exactly that behavior.

### Admin frontend

Pages: `/login` (admin sign-in; an authenticated non-admin is refused), `/dashboard`, `/qr-codes` (list with filters, pagination, refresh, create modal, deactivate/reactivate with confirmation), `/qr-codes/:id` (details + status actions), `/products` (searchable table with create/edit/delete, QR-reference states), `/products/:id` (details + edit + guarded delete), `/clothing` (searchable/filterable catalog table with create/edit/delete), `/clothing/:id` (details + edit + guarded delete), `/users` (search/filter table), `/users/:id` (details + status actions). A normal user's token never grants access — the UI hides nothing, the backend enforces everything.

The Clothing page's category dropdown is populated from `GET /admin/clothing/categories` — the administrator picks a named category (the form shows which avatar slot it equips into) and the actual `category_id` is sent; no manual ids, and the slot is never editable. The availability dropdown offers exactly the three existing enum values.

### Manual QR workflow test

1. Start the backend and the admin frontend (commands below).
2. Grant one of your users admin: `UPDATE users SET is_admin = true WHERE username = '<admin>';`
3. Open http://localhost:5173 → log in as that administrator.
4. Open **QR Codes** → view any existing codes (or none yet).
5. Click **Create QR** → pick a product, enter a coin value → **Create** → the new `PB-…` code appears in the list.
6. Open its **View** page → see code, product, coins, status, created/expiry.
7. Use **Deactivate / Reactivate** and confirm in the dialog; reload to confirm the change stuck.
8. Log out, log back in as a non-admin user → the panel refuses access (403 → redirect to login with the "not an administrator" message).

### Manual user-management workflow test

1. With the panel open as an administrator, click **Users** in the sidebar.
2. See the user table (username, email, coins, streak, status, created).
3. Type in the search box → the list narrows to matching usernames/emails.
4. Filter by **Status** → Active / Inactive.
5. Click **View** on any user → see profile picture/initials, biography, balances, and dates.
6. Click **Deactivate** on that user (with confirmation) → the status flips to inactive, and the user's account is locked out of the app immediately (they can no longer log in).
7. **Reactivate** → the status flips back to active and login is restored.
8. Search yourself (the admin) → no **Deactivate** button appears; the backend also refuses self-deactivation with a 400.
9. As a plain (non-admin) account, attempt to open `/users` in the browser → the panel redirects to login and every `/admin/users` API call returns 403.

See `test_admin_users.py` for the automated coverage of all of the above.

### Manual product-management workflow test

1. With the panel open as an administrator, click **Products** in the sidebar.
2. Click **New product** → enter a name and a unique SKU → **Create** → the row appears in the table with `QR refs = 0`.
3. Try **New product** again with the **same SKU** → the backend rejects it (`409`, "A product with this SKU already exists").
4. Type in the search box → the list narrows to matching names/SKUs.
5. Click **View** on the product → detail page shows `product_id`, name, SKU, `created_at`, and QR reference count.
6. Click **Edit product** → change the name (and/or SKU) → **Save changes** → the detail page reflects the change; `product_id`/`created_at` stay identical.
7. In **QR Codes**, click **Create QR** and pick this product → **Create** → now `QR refs` on the product becomes `1`.
8. Back on the product detail/table, the **Delete** button is disabled ("Referenced by QR codes"); even a direct `DELETE /admin/products/{id}` returns `409`.
9. Create a second, unreferenced product → its **Delete** button is enabled → delete it with confirmation → the row disappears.

See `test_admin_products.py` for the automated coverage of all of the above.

### Manual clothing-management workflow test

1. With the panel open as an administrator, click **Clothing** in the sidebar.
2. See the catalog table (thumbnail, name, category, slot, price, availability pill, created) — the seeded items from **Seed data** are all there, including `UNAVAILABLE`/`UPCOMING` ones that the public shop hides.
3. Type in the search box → the list narrows to matching names/descriptions; use the **Category** and **Availability** dropdowns to filter.
4. Click **New clothing** → pick a category from the dropdown (populated by the backend; it shows the avatar slot), enter a name, price, image URL → **Create clothing** → the row appears with an `available` pill.
5. Click **View** on the item → the detail page shows every field including `item_id`, collection id and created date.
6. Click **Edit clothing** → change the price and set availability to **Unavailable** → **Save changes** → the detail page reflects both.
7. Log in as a normal user (or use their token): `GET /clothing` no longer lists the item, and `POST /clothing/{item_id}/purchase` returns `409` — the item is off the shelf without being destroyed.
8. Edit the item back to **Available** → a user can browse and buy it again.
9. Try **Delete** on an item a user has purchased → the backend refuses with `409` ("…Mark it UNAVAILABLE instead") and the ownership record survives; delete a never-purchased item → `204` and it disappears.

See `test_admin_clothing.py` for the automated coverage of all of the above.

## Test users

Three fixed accounts to use for all manual testing (Postman, cURL, etc.). They are **not** seeded into the database — you must register each one once with `POST /auth/register`. They persist after that, so you can log in with the same credentials every time and never have to re-register.

| Username | Email | Password | Role |
| --- | --- | --- | --- |
| `alice` | `alice@example.com` | `Password123!` | The follower — logs in and performs follow/unfollow |
| `bob` | `bob@example.com` | `Password123!` | The followee — the target Alice follows |
| `carol` | `carol@example.com` | `Password123!` | A third account for cross-checks (e.g. "not followed" → 404, search hits) |

Register all three once:

```json
POST /auth/register
{ "username": "alice", "email": "alice@example.com", "password": "Password123!" }
{ "username": "bob",   "email": "bob@example.com",   "password": "Password123!" }
{ "username": "carol", "email": "carol@example.com", "password": "Password123!" }
```

Notes:
- If a username is already taken (e.g. a previous run left one behind), registration returns `409` — just log in with those credentials instead.
- To discover a user's `user_id`, log in and call `GET /users/me`, or search with `GET /users/search?q=bob`.

## Postman test sequence

1. `POST /auth/register` with `{username, email, password}` → `201`.
2. `POST /auth/login` with `{username, password}` → copy `access_token`.
3. `GET /users/me` with header `Authorization: Bearer <token>` → `200` with the user's profile.
4. `PATCH /users/me` with header `Authorization: Bearer <token>` and a body like `{"biography": "My new biography"}` → `200` with the updated profile.
5. `GET /users/search?q=alex` with header `Authorization: Bearer <token>` → `200` with a list of matching public profiles (excludes yourself).
6. Negative cases: wrong password → `401`, unknown user → `401`, no header → `401`, garbage token → `401`, duplicate username → `409`, `{"username": ""}` → `422`, search with `q=` (empty) → `422`.

### Clothing browsing (Phase 3)

The catalog is seeded by `alembic upgrade head` (see **Seed data**), so it is available immediately. Log in as any test user and use their token:

| Request | Expected |
| --- | --- |
| `GET /clothing` | `200` — paginated AVAILABLE-only catalog (`total` 12, ordering `created_at DESC`, `item_id DESC` tiebreak) |
| `GET /clothing?category_id=3` | `200` — only Tops items (2 available: Cola Classic Tee, Polar Hoodie) |
| `GET /clothing?limit=5&offset=5` | `200` — page of ≤ 5 items after skipping 5 |
| `GET /clothing?category_id=2` | `200` — only the AVAILABLE Hats return (Polar Snapback, Sunrise Bucket Hat); Winter Toque Deluxe (`UNAVAILABLE`) never appears |
| `GET /clothing?category_id=999` | `422` (out of SMALLINT range) |
| `GET /clothing?category_id=32000` | `404` (in range but nonexistent) |
| `GET /clothing` (no `Authorization` header) | `401` |
| `GET /clothing?limit=0` / `offset=-1` / `category_id=abc` | `422` |

Verify in the database:

```sql
SELECT name, category_id, price, availability_status FROM clothing_items ORDER BY created_at, item_id;
```

### Clothing purchase (Phase 3)

Purchases spend real coins, so first give your test user a balance directly in the DB (there is no endpoint that mints coins yet):

```sql
UPDATE users SET coin_balance = 1000 WHERE username = 'alice';
```

Then, with alice's token:

| Request | Expected |
| --- | --- |
| `POST /clothing/{item_id}/purchase` (a seeded AVAILABLE item, e.g. Polar Shades) | `200` with `message`, `wardrobe_id`, `item`, `amount_spent: 120`, `remaining_balance: 880`, `transaction_id` |
| Same request again | `409` ("You already own this clothing item") |
| `POST /clothing/{item_id}/purchase` for Winter Toque Deluxe (`UNAVAILABLE`) | `409` ("not available for purchase") |
| `POST /clothing/{item_id}/purchase` for Neon Faux Hawk (`UPCOMING`) | `409` ("not available for purchase") |
| `POST /clothing/{random-uuid}/purchase` | `404` |
| `POST /clothing/not-a-uuid/purchase` | `422` |
| Any purchase without the `Authorization` header | `401` |

To see an insufficient-balance rejection, drop alice's balance below an item's price and retry:

```sql
UPDATE users SET coin_balance = 50 WHERE username = 'alice';
-- POST /clothing/{polar_hoodie_id}/purchase -> 400 "Insufficient coin balance"
```

Verify in the database after a successful buy:

```sql
-- Ownership row exists:
SELECT * FROM user_wardrobe WHERE user_id = '<alice_user_id>';

-- Exactly one DEBIT ledger row per purchase, consistent with the balance:
SELECT t.amount, t.balance_after, t.type_name, w.wardrobe_id
FROM coin_transactions t
JOIN coin_transaction_types tt ON tt.type_id = t.type_id
JOIN user_wardrobe w ON w.wardrobe_id = t.wardrobe_id
WHERE t.user_id = '<alice_user_id>' AND tt.type_name = 'clothing_purchase';

-- users.coin_balance equals the newest ledger row's balance_after:
SELECT coin_balance FROM users WHERE user_id = '<alice_user_id>';
```

### Wardrobe (Phase 4, part 1)

Continuing from the purchase flow above (alice owns Polar Shades), with alice's token:

| Request | Expected |
| --- | --- |
| `GET /wardrobe` | `200` with `items` containing one entry (`wardrobe_id`, `purchased_at`, the Polar Shades item in the catalog shape), `total: 1`, `limit: 20`, `offset: 0` |
| `GET /wardrobe?limit=100&offset=0` | `200`, same entries, explicit pagination echoed back |
| `GET /wardrobe?limit=0` / `limit=101` / `limit=-1` / `offset=-1` | `422` |
| Any request without the `Authorization` header | `401` |
| Log in as **bob** and call `GET /wardrobe` | `200` with `"items": []`, `"total": 0` — bob cannot see alice's purchases |

There is no `user_id` parameter on this endpoint: the wardrobe is always the caller's own. A smuggled `?user_id=<alice_user_id>` on bob's request changes nothing.

### Avatar (Phase 5)

Continuing from above (alice owns Polar Shades and has an avatar created; equip Polar Shades first so there is something to render):

| Request | Expected |
| --- | --- |
| `GET /avatar` with alice's token | `200` with `avatar_id` and an `equipment` map containing all six slot keys (`hair`, `hat`, `top`, `bottom`, `shoes`, `accessory`); `accessory` holds `{ equipped_at, item }` with the Polar Shades in the catalog shape, the other five slots are `null` |
| `GET /avatar` without the `Authorization` header | `401` |
| `GET /avatar?user_id=<bob_user_id>` with alice's token | `200` — still alice's avatar; query params are ignored |
| Log in as **bob** (no purchases) and call `GET /avatar` | `404` "Avatar not found" if bob has no avatar row — registration never creates one |

Verify in the database that the response mirrors reality:

```sql
SELECT e.slot, e.equipped_at, i.name
FROM avatar_equipment e
LEFT JOIN clothing_items i ON i.item_id = e.item_id
WHERE e.avatar_id = '<alice_avatar_id>';
```

### Complete follow flow (uses the test users above)

1. Log in as **alice** (`POST /auth/login` with `{username: "alice", password: "Password123!"}`) and copy her `access_token`.
2. Log in as **bob** and call `GET /users/me` to get his `user_id`, or search from alice's account with `GET /users/search?q=bob`.
3. `POST /users/{bob_user_id}/follow` with alice's `Authorization: Bearer <token>` → `201` with `{"is_following": true}`.
4. `GET /users/{bob_user_id}/follow-status` with the same token → `200` with `{"is_following": true}`.
5. `DELETE /users/{bob_user_id}/follow` with the same token → `200` with `{"is_following": false}`.
6. `GET /users/{bob_user_id}/follow-status` again → `200` with `{"is_following": false}`.

Follow negative cases to try:
- Repeat step 3 twice → second call is `409`.
- `POST /users/{alice_user_id}/follow` (alice following herself) → `400`.
- `POST /users/{nonexistent_uuid}/follow` → `404`.
- `DELETE /users/{carol_user_id}/follow` when alice is not following carol → `404`.
- Call any follow endpoint without the `Authorization` header → `401`.

Verify directly in the database:

```sql
SELECT * FROM follows;
```

### QR redemption (Step 3 — validate, redeem, award coins)

There is no admin endpoint to create QR codes yet, so seed test data directly in the DB (or via psql):

```sql
-- One product + four qr_codes in every state.
INSERT INTO products (name, sku) VALUES ('Cola 330ml', 'SKU-MANUAL-TEST')
RETURNING product_id;

-- Copy the product_id from above into the queries below. For the redeemed
-- code, also copy alice's user_id (from GET /users/me after logging in).
-- 1) valid, active, expires tomorrow:
INSERT INTO qr_codes (code, product_id, coin_value, status, expires_at)
VALUES ('COLA-MANUAL-VALID', '<product_id>', 10, 'ACTIVE', now() + interval '1 day');

-- 2) redeemed:
INSERT INTO qr_codes (code, product_id, coin_value, status,
                      redeemed_by_user_id, redeemed_at)
VALUES ('COLA-MANUAL-REDEEMED', '<product_id>', 5, 'REDEEMED',
        '<a_user_id>', now());

-- 3) expired by status:
INSERT INTO qr_codes (code, product_id, coin_value, status)
VALUES ('COLA-MANUAL-EXPIRED', '<product_id>', 5, 'EXPIRED');

-- 4) active but past expires_at:
INSERT INTO qr_codes (code, product_id, coin_value, status, expires_at)
VALUES ('COLA-MANUAL-OVERDUE', '<product_id>', 7, 'ACTIVE', now() - interval '1 hour');
```

Then in Postman (log in as **alice** from the Test users table and use her token):

| Request | Expected |
| --- | --- |
| `POST /qr/redeem` `{"code": "COLA-MANUAL-VALID"}` | `200` `{"message": "Code redeemed successfully", "coins_earned": 10, "balance": <alice's balance + 10>}` |
| `POST /qr/redeem` `{"code": "COLA-MANUAL-VALID"}` again | `409` |
| `POST /qr/redeem` `{"code": "COLA-MANUAL-REDEEMED"}` | `409` |
| `POST /qr/redeem` `{"code": "COLA-MANUAL-EXPIRED"}` | `410` |
| `POST /qr/redeem` `{"code": "COLA-MANUAL-OVERDUE"}` | `410` |
| `POST /qr/redeem` `{"code": "COLA-NO-SUCH-CODE"}` | `404` |
| `POST /qr/redeem` `{"code": "COLA-MANUAL-VALID"}` (no `Authorization` header) | `401` |
| `POST /qr/redeem` `{"code": ""}` | `422` |

Confirm the code is now redeemed in the database (and that alice is the owner):

```sql
SELECT code, status, redeemed_by_user_id, redeemed_at
FROM qr_codes
WHERE code = 'COLA-MANUAL-VALID';  -- status = REDEEMED, redeemed_by = alice's id, redeemed_at set
```

And that alice was credited exactly once:

```sql
SELECT amount, balance_after, qr_id
FROM coin_transactions
WHERE user_id = '<alice_user_id>';  -- one row per redeemed code, amount = coin_value
```