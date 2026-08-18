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
│   ├── user.py           # UserRegister/UserUpdate (input), UserRead (output)
│   └── token.py          # LoginRequest (input), Token (output)
├── routers/
│   ├── auth.py           # POST /auth/register, POST /auth/login
│   └── users.py          # GET /users/me, PATCH /users/me (protected)
├── test_db.py            # Manual DB connectivity check
├── test_register.py      # End-to-end registration checks
├── test_auth_flow.py     # End-to-end login + JWT checks
└── test_update_profile.py# End-to-end PATCH /users/me checks
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
venv/Scripts/python -m app.test_db

# Registration end-to-end (creates unique test users, then deletes them)
venv/Scripts/python -m app.test_register

# Login + JWT end-to-end (register -> login -> /users/me, plus 401 cases)
venv/Scripts/python -m app.test_auth_flow

# PATCH /users/me end-to-end (single/multi-field update, duplicates, 401s, 422s)
venv/Scripts/python -m app.test_update_profile
```

`test_register.py` currently verifies: successful registration, password stored as an Argon2 hash (not plain text), duplicate username rejected, duplicate email rejected (case-insensitively), invalid payloads rejected by Pydantic, and no password/hash leakage in responses. All test users are cleaned up afterward.

`test_auth_flow.py` verifies: login returns a token, the JWT contains only `sub`/`iat`/`exp` with no sensitive data, `GET /users/me` works with a valid token, and returns `401` for wrong password, nonexistent user, missing/invalid/expired token, and inactive users. All test users are cleaned up afterward.

`test_update_profile.py` verifies: updating one field leaves the others unchanged (username/biography/profile updates all persisted to the DB), clearing a field with `null`, duplicate username → `409` (including the case-insensitive `CITEXT` case), reusing your own username allowed, missing token → `401`, empty/invalid/`null` username → `422`, and no `password_hash` in the response. All test users are cleaned up afterward.

## Postman test sequence

1. `POST /auth/register` with `{username, email, password}` → `201`.
2. `POST /auth/login` with `{username, password}` → copy `access_token`.
3. `GET /users/me` with header `Authorization: Bearer <token>` → `200` with the user's profile.
4. `PATCH /users/me` with header `Authorization: Bearer <token>` and a body like `{"biography": "My new biography"}` → `200` with the updated profile.
5. Negative cases: wrong password → `401`, unknown user → `401`, no header → `401`, garbage token → `401`, duplicate username → `409`, `{"username": ""}` → `422`.