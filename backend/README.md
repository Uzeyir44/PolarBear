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
│   └── coin.py           # CoinBalance / CoinTransactionRead / QRTransactionReference (output)
├── routers/
│   ├── auth.py           # POST /auth/register, POST /auth/login
│   ├── users.py          # /users/me, /users/me/coins, /users/me/transactions,
│   │                     # PATCH /users/me, /users/search, follow/unfollow/follow-status
│   └── qr.py             # POST /qr/redeem — Step 3: validate + redeem + award coins (protected)
tests/                   # End-to-end test scripts (run with python -m tests.<name>)
├── test_db.py            # Manual DB connectivity check
├── test_register.py      # End-to-end registration checks
├── test_auth_flow.py     # End-to-end login + JWT checks
├── test_update_profile.py# End-to-end PATCH /users/me checks
├── test_user_search.py   # End-to-end GET /users/search checks
├── test_follow.py        # End-to-end follow/unfollow/follow-status checks
├── test_qr_redeem.py     # End-to-end POST /qr/redeem redeem checks
├── test_qr_redeem_coins.py# End-to-end coin-award checks (Step 3)
└── test_user_coins.py    # End-to-end GET /users/me/coins and /users/me/transactions
alembic/                  # Migrations (initial_schema)
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
4. Create the user row and commit.
5. Handle database-level unique-violations safely as a fallback (race condition) by mapping the violated index to a clear `409`.
6. Return only safe user info via `UserRead` — the response **never** includes `password_hash` or the password.

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

# Registration end-to-end (creates unique test users, then deletes them)
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
```

`test_register.py` currently verifies: successful registration, password stored as an Argon2 hash (not plain text), duplicate username rejected, duplicate email rejected (case-insensitively), invalid payloads rejected by Pydantic, and no password/hash leakage in responses. All test users are cleaned up afterward.

`test_auth_flow.py` verifies: login returns a token, the JWT contains only `sub`/`iat`/`exp` with no sensitive data, `GET /users/me` works with a valid token, and returns `401` for wrong password, nonexistent user, missing/invalid/expired token, and inactive users. All test users are cleaned up afterward.

`test_update_profile.py` verifies: updating one field leaves the others unchanged (username/biography/profile updates all persisted to the DB), clearing a field with `null`, duplicate username → `409` (including the case-insensitive `CITEXT` case), reusing your own username allowed, missing token → `401`, empty/invalid/`null` username → `422`, and no `password_hash` in the response. All test users are cleaned up afterward.

`test_user_search.py` verifies: partial (prefix and mid-string) matches, case-insensitive matching, no matches → `[]`, the caller excluded from their own results, missing/invalid/expired token → `401`, missing/empty/too-long `q` → `422`, `limit` bounds → `422`, inactive users excluded, `%`-wildcards treated literally, and responses exposing only the four public fields. All test users are cleaned up afterward.

`test_follow.py` verifies: missing/invalid/expired token → `401`, follow nonexistent user → `404`, follow inactive user → `400`, follow yourself → `400`, valid follow → `201` with the row actually in the DB, duplicate follow → `409` with no duplicate row, a body-supplied `follower_id` is ignored (the follower always comes from the JWT), follow-status returns `is_following: true`/`false`, unfollow someone not followed → `404`, valid unfollow → `200` with the row actually deleted, unfollow again → `404`, and a second user can follow the same target. All test users (and their follow rows, via cascade) are cleaned up afterward.

`test_qr_redeem.py` verifies: missing/invalid/expired token → `401`, nonexistent code → `404`, already-redeemed code → `409`, expired code → `410`, active-but-past-`expires_at` code → `410`, valid active code → `200` with `message`/`coins_earned`/`balance` **and** the row actually updated in the DB (status `REDEEMED`, `redeemed_by_user_id` = caller, `redeemed_at` set), the user's `coin_balance` increased by `coin_value` with exactly one `coin_transactions` row, the response exposes **only** those three safe fields, and the same code can't be redeemed again by the same or a different user → `409` with the original owner preserved. Test products, qr_codes, users, and their coin transactions are cleaned up afterward.

`test_qr_redeem_coins.py` verifies the coin award in depth: a valid redemption credits exactly `coin_value` coins and reports the correct new balance; the QR row becomes `REDEEMED`; exactly one `coin_transactions` row is created with the right `amount`, `balance_after`, `user_id`, `type_id` (`qr_redemption`), and `qr_id`; a second redemption correctly stacks onto the first (`balance_after` is the running total); redeeming the same code again → `409` with no second credit and still one ledger row; a mid-transaction failure (the `qr_redemption` type temporarily made unresolvable) rolls back the claim, the credit, and the ledger; and two concurrent attempts on the same code — both through two real DB connections and through concurrent HTTP calls — result in exactly one success and one `409`, with the coins awarded exactly once. All test data is cleaned up afterward.

`test_user_coins.py` verifies the read side: missing/invalid/expired token → `401` on both endpoints; `GET /users/me/coins` returns the exact `coin_balance` from PostgreSQL; a client-supplied `user_id` query param is ignored; `GET /users/me/transactions` returns all of the user's rows in newest-first order with every row exposing only the whitelisted fields (`transaction_id`, `amount`, `balance_after`, `transaction_type`, `direction`, `created_at`, `qr`, and the nullable `competition_id`/`wardrobe_id`/`vote_id`); a real QR redemption surfaces with its QR code and product name; debit rows serialize as `direction: DEBIT` with negative amounts; `limit`/`offset` pagination tiles the full history with no overlap and empties beyond the end; another user's transactions are invisible (user B sees only B's rows) and balances are separate; empty history → `[]`; and invalid `limit`/`offset` (0, 51, -1, non-numeric, negative offset) → `422`. All test data is cleaned up afterward.

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