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
| Input/output validation | Pydantic schemas |

## Setup

1. Create a PostgreSQL database:
   ```sql
   CREATE DATABASE cci_db;
   CREATE EXTENSION IF NOT EXISTS citext;
   ```
2. Copy the connection string into `.env`:
   ```
   DATABASE_URL=postgresql+psycopg://postgres:<password>@localhost:5432/cci_db
   ```
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
├── core/
│   ├── config.py         # Settings (reads DATABASE_URL from .env)
│   ├── database.py       # engine, SessionLocal, Base, get_db() dependency
│   └── security.py       # PasswordHasher (Argon2id hashing/verification)
├── models/               # SQLAlchemy ORM models — 19 tables (complete schema)
├── schemas/
│   └── user.py           # Pydantic schemas: UserRegister (input), UserRead (output)
├── routers/
│   └── auth.py           # POST /auth/register
├── test_db.py            # Manual DB connectivity check
└── test_register.py      # End-to-end registration checks
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
The first part of the authentication system. Login, JWT, and refresh tokens are **not** implemented yet.

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

## Security notes

- Passwords are hashed with **Argon2id** via `pwdlib` (`app/core/security.py`), using a reusable `PasswordHasher` so routes never contain hashing logic.
- Only the hash is stored in `users.password_hash`.
- Hashes are never returned by the API (`password_hash` is not a field in `UserRead`).
- Passwords are never logged.

## Tests

Hand-written scripts (no framework) that exercise the real HTTP + DB stack. Run them from the `backend/` directory:

```bash
# DB connectivity
venv/Scripts/python -m app.test_db

# Registration end-to-end (creates unique test users, then deletes them)
venv/Scripts/python -m app.test_register
```

`test_register.py` currently verifies: successful registration, password stored as an Argon2 hash (not plain text), duplicate username rejected, duplicate email rejected (case-insensitively), invalid payloads rejected by Pydantic, and no password/hash leakage in responses. All test users are cleaned up afterward.