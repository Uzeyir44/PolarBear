"""
End-to-end checks of competition creation & lifecycle — Phase 6, Part 2:

  * accepting a PENDING request atomically creates an ACTIVE competition
  * the competition copies the request's parties/duration, prizes are 0,
    winner NULL, start_time set, end_time = start_time + duration (DB generated)
  * duplicate/concurrent acceptance can never create two competitions
  * accept remains opponent-only and authorization failures create nothing
  * GET /competitions/{competition_id} is private to the two participants,
    requires auth, and exposes only safe fields

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_competition_creation

A = challenger, B = opponent (the only user allowed to accept), C = outsider.
All test rows (competitions -> requests -> users) are cleaned up afterwards;
as with the Part 1 test, competitions must be removed first because their FK
back to competition_requests is ondelete RESTRICT.
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

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.main import app
from app.models import Competition, CompetitionRequest, CompetitionStatus, User

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

USER_A = f"cmp_a_{RUN_ID}"
USER_B = f"cmp_b_{RUN_ID}"
USER_C = f"cmp_c_{RUN_ID}"
USERS = [USER_A, USER_B, USER_C]


def report(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({extra})" if extra else ""))


def cleanup() -> None:
    with SessionLocal() as db:
        ids = db.execute(select(User.user_id).where(User.username.in_(USERS))).scalars().all()
        if ids:
            reqs = db.execute(
                select(CompetitionRequest).where(
                    CompetitionRequest.challenger_id.in_(ids)
                    | CompetitionRequest.opponent_id.in_(ids)
                )
            ).scalars().all()
            req_ids = [r.request_id for r in reqs]
            if req_ids:
                for c in db.execute(
                    select(Competition).where(Competition.request_id.in_(req_ids))
                ).scalars().all():
                    db.delete(c)
            for r in reqs:
                db.delete(r)
        for row in db.execute(select(User).where(User.username.in_(USERS))).scalars().all():
            db.delete(row)
        db.commit()
    print(f"\nCleaned up {len(USERS)} test user(s).")


def user_id_of(db, username: str) -> str:
    return str(db.execute(select(User.user_id).where(User.username == username)).scalar_one())


def send_request(token: str, opponent_id: str, duration: int = 60) -> str:
    r = client.post(
        "/competition-requests",
        headers={"Authorization": f"Bearer {token}"},
        json={"opponent_id": opponent_id, "duration_minutes": duration},
    )
    return r.json()["request_id"]


def accept(token: str, request_id: str) -> object:
    return client.post(
        f"/competition-requests/{request_id}/accept",
        headers={"Authorization": f"Bearer {token}"},
    )


def get_comp(token: str, competition_id: str) -> object:
    return client.get(
        f"/competitions/{competition_id}", headers={"Authorization": f"Bearer {token}"}
    )


def count_competitions(request_id: str) -> int:
    with SessionLocal() as db:
        return len(
            db.execute(
                select(Competition).where(Competition.request_id == uuid.UUID(request_id))
            ).scalars().all()
        )


def request_status(request_id: str) -> str:
    with SessionLocal() as db:
        return db.get(CompetitionRequest, uuid.UUID(request_id)).status.name


def competition_row(request_id: str) -> object:
    with SessionLocal() as db:
        return db.execute(
            select(Competition)
            .where(Competition.request_id == uuid.UUID(request_id))
            .options(joinedload(Competition.status))
        ).scalar_one()


def active_status_id() -> int:
    with SessionLocal() as db:
        return db.scalar(
            select(CompetitionStatus.status_id).where(CompetitionStatus.status_name == "active")
        )


results = []

# --- Setup ------------------------------------------------------------------
for username in USERS:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
    )
    results.append((f"setup register {username}", r.status_code == 201, str(r.status_code)))

with SessionLocal() as db:
    A_ID = user_id_of(db, USER_A)
    B_ID = user_id_of(db, USER_B)
    C_ID = user_id_of(db, USER_C)

TOKEN_A = client.post("/auth/login", json={"username": USER_A, "password": PASSWORD}).json()["access_token"]
TOKEN_B = client.post("/auth/login", json={"username": USER_B, "password": PASSWORD}).json()["access_token"]
TOKEN_C = client.post("/auth/login", json={"username": USER_C, "password": PASSWORD}).json()["access_token"]

try:
    # --- 1-11. Accepting a PENDING request creates exactly one competition ------------
    created_req = send_request(TOKEN_A, B_ID, 60)
    ack = accept(TOKEN_B, created_req)
    results.append(("accept creates competition (accept -> 200)", ack.status_code == 200, str(ack.status_code)))
    results.append(("request becomes ACCEPTED", request_status(created_req) == "ACCEPTED", request_status(created_req)))
    results.append(("exactly one competition per request", count_competitions(created_req) == 1, str(count_competitions(created_req))))

    comp = competition_row(created_req)
    comp_id = str(comp.competition_id)
    results.append(("competition references the correct request", str(comp.request_id) == created_req, ""))
    results.append(("challenger copied correctly", str(comp.challenger_id) == A_ID, ""))
    results.append(("opponent copied correctly", str(comp.opponent_id) == B_ID, ""))
    results.append(("duration copied correctly", comp.duration_minutes == 60, str(comp.duration_minutes)))
    results.append(("status is 'active'", comp.status.status_name == "active", comp.status.status_name))
    results.append(("total_votes starts at 0", comp.total_votes == 0, str(comp.total_votes)))
    results.append(("winner_id is NULL", comp.winner_id is None, ""))
    results.append(("start_time is populated", comp.start_time is not None, ""))
    # end_time is a DB GENERATED column: naive start_time + duration_minutes in
    # session wall-clock. Reading the stored TZ-aware end_time back as naive
    # wall-clock minus the naive start_time is exactly "duration minutes".
    delta = comp.end_time.replace(tzinfo=None) - comp.start_time
    results.append(("end_time = start_time + duration",
                    comp.end_time is not None and delta == timedelta(minutes=60), str(delta)))
    results.append(("prize_pool starts at 0 (DB server default)", comp.prize_pool == 0, str(comp.prize_pool)))

    # --- 18. Retrieval: participants can read it -------------------------------------
    for name, token, who in (("participant challenger", TOKEN_A, "A"), ("participant opponent", TOKEN_B, "B")):
        r = get_comp(token, comp_id)
        js = r.json()
        ok = (
            r.status_code == 200
            and js["competition_id"] == comp_id
            and js["request_id"] == created_req
            and js["challenger"]["user_id"] == A_ID
            and js["opponent"]["user_id"] == B_ID
            and js["status"] == "active"
            and js["prize_pool"] == 0
            and js["total_votes"] == 0
            and js["winner_id"] is None
            and js["duration_minutes"] == 60
            and js["start_time"] is not None
            and js["end_time"] is not None
            and js["created_at"] is not None
        )
        results.append((f"retrieve as {who} -> 200 with full safe payload", ok, str(r.status_code)))

    # --- 21. No sensitive user info exposed --------------------------------------------
    r = get_comp(TOKEN_A, comp_id)
    text = str(r.json())
    allowed = {"competition_id", "request_id", "challenger", "opponent", "status", "prize_pool",
               "total_votes", "winner_id", "duration_minutes", "start_time", "end_time", "created_at"}
    user_allowed = {"user_id", "username", "profile_picture_url", "biography"}
    extra = set(r.json().keys()) - allowed
    extra |= set(r.json()["challenger"].keys()) - user_allowed
    extra |= set(r.json()["opponent"].keys()) - user_allowed
    ok = (
        not extra
        and "password_hash" not in text
        and "email" not in text
        and "coin_balance" not in text
        and "winning_streak" not in text
    )
    results.append(("no sensitive user data in competition response", ok, "" if ok else str(extra)))

    # --- 20. Unauthenticated -> 401 -------------------------------------------------------
    r = client.get(f"/competitions/{comp_id}")
    results.append(("unauthenticated retrieve -> 401", r.status_code == 401, str(r.status_code)))

    # --- 19. Non-participant -> 403 ---------------------------------------------------------
    r = get_comp(TOKEN_C, comp_id)
    results.append(("non-participant retrieve -> 403", r.status_code == 403, str(r.status_code)))

    # --- 15/16/17. Authorization on acceptance -----------------------------------------------
    authz_req = send_request(TOKEN_A, B_ID, 30)
    r = accept(TOKEN_A, authz_req)
    results.append(("challenger cannot accept -> 403, no competition",
                    r.status_code == 403 and count_competitions(authz_req) == 0, str(r.status_code)))
    results.append(("rejected challenger accept leaves request PENDING", request_status(authz_req) == "PENDING", ""))
    r = accept(TOKEN_C, authz_req)
    results.append(("unrelated user cannot accept -> 403, no competition",
                    r.status_code == 403 and count_competitions(authz_req) == 0, str(r.status_code)))

    # --- 13/18. Same request can't produce two competitions ------------------------------------
    r = accept(TOKEN_B, created_req)
    results.append(("accept again on ACCEPTED request -> 409, still one competition",
                    r.status_code == 409 and count_competitions(created_req) == 1, str(r.status_code)))

    # --- 12. Atomicity: competition INSERT failure rolls back request acceptance --------------
    atomic_req = send_request(TOKEN_A, B_ID, 30)
    with SessionLocal() as db:
        db.add(
            Competition(
                request_id=uuid.UUID(atomic_req),
                challenger_id=uuid.UUID(A_ID),
                opponent_id=uuid.UUID(B_ID),
                status_id=active_status_id(),
                duration_minutes=30,
                start_time=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        db.commit()
    r = accept(TOKEN_B, atomic_req)
    ok = (
        r.status_code == 409                     # unique(request_id) fired
        and request_status(atomic_req) == "PENDING"   # request acceptance rolled back
        and count_competitions(atomic_req) == 1       # only the pre-existing one survived
    )
    results.append(("competition-insert failure rolls back acceptance, request stays PENDING",
                    ok, f"status={r.status_code}"))

    # --- 14. HTTP concurrency: two simultaneous accepts -> one 200, one 409 -----------------
    # Use a request A -> C (no existing active matchup between A and C); the
    # pair A vs B already holds an active competition from earlier in this test.
    conc_req = send_request(TOKEN_A, C_ID, 30)
    barrier = threading.Barrier(2)
    outcomes = []

    def race_accept() -> None:
        barrier.wait()
        outcomes.append(accept(TOKEN_C, conc_req).status_code)

    threads = [threading.Thread(target=race_accept, name=f"race_accept_{i}") for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = (
        sorted(outcomes) == [200, 409]
        and count_competitions(conc_req) == 1
        and request_status(conc_req) == "ACCEPTED"
    )
    results.append(("concurrent accepts -> exactly one 200 + one 409, one competition",
                    ok, f"outcomes={outcomes}"))

finally:
    cleanup()

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)