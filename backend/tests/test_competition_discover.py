"""
End-to-end checks of public competition discovery — Phase 6, Part 4A:

  * GET /competitions/discover requires auth (401 unauth) and returns ONLY
    ACTIVE competitions between TWO OTHER users (the caller's own
    competitions as challenger OR opponent are excluded — the self-vote rule)
  * completed competitions excluded
  * competitions with an inactive challenger or opponent excluded
  * envelope {items, total, limit, offset} with the standard public-user
    shape (UserPublic) — never email/password_hash/coin_balance/is_active
  * default pagination (limit 20), max limit enforced, invalid values -> 422
  * deterministic ordering: end_time ASC (ending soonest first),
    competition_id tiebreak

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_competition_discover

Viewer C must discover exactly [A-vs-B, A-vs-D] and not see any of the
others. Competitions are built through the real request/accept flow; the
completed flip and the inactive-user flips are applied directly in the DB
(as other tests manipulate user state directly). All test rows
(competitions -> requests -> users) are cleaned up afterwards.
"""
import time
import uuid
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import Competition, CompetitionRequest, CompetitionStatus, User

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

NAMES = ["A", "B", "C", "D", "E", "F", "X", "Y"]
USERS = {n: f"disc_{n.lower()}_{RUN_ID}" for n in NAMES}


def report(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({extra})" if extra else ""))


def cleanup() -> None:
    with SessionLocal() as db:
        ids = db.execute(select(User.user_id).where(User.username.in_(USERS.values()))).scalars().all()
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
        for row in db.execute(select(User).where(User.username.in_(USERS.values()))).scalars().all():
            db.delete(row)
        db.commit()
    print(f"\nCleaned up {len(USERS)} test user(s).")


def register(username: str) -> None:
    client.post(
        "/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
    )


def login(username: str) -> str:
    return client.post("/auth/login", json={"username": username, "password": PASSWORD}).json()["access_token"]


def send_request(token: str, opponent_id: str, duration: int = 30) -> str:
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


def discover(token: str, query: str = "") -> object:
    return client.get(f"/competitions/discover{query}", headers={"Authorization": f"Bearer {token}"})


def competition_id_of(request_id: str) -> str:
    with SessionLocal() as db:
        return str(
            db.execute(
                select(Competition.competition_id).where(Competition.request_id == uuid.UUID(request_id))
            ).scalar_one()
        )


def _status_id(name: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(CompetitionStatus.status_id).where(CompetitionStatus.status_name == name))


def mark_completed(request_id: str) -> None:
    with SessionLocal() as db:
        comp = db.execute(
            select(Competition).where(Competition.request_id == uuid.UUID(request_id))
        ).scalar_one()
        comp.status_id = _status_id("completed")
        db.commit()


def deactivate(username: str) -> None:
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one()
        user.is_active = False
        db.commit()


def market(challenger: str, opponent: str, duration: int = 30) -> str:
    """challenger sends a request to opponent; opponent accepts; returns request_id."""
    req = send_request(TOKENS[challenger], IDS[opponent], duration)
    r = accept(TOKENS[opponent], req)
    assert r.status_code == 200, f"setup accept {challenger}->{opponent} failed: {r.status_code}"
    return req


results = []

# --- Setup ---------------------------------------------------------------
for n, username in USERS.items():
    register(username)
TOKENS = {n: login(USERS[n]) for n in USERS}
IDS = {}
with SessionLocal() as db:
    for n in USERS:
        IDS[n] = str(db.execute(select(User.user_id).where(User.username == USERS[n])).scalar_one())

try:
    # --- Build the competition landscape ----------------------------------------------
    # comp5: E vs F, then completed (never appears).
    comp5 = market("E", "F")
    mark_completed(comp5)
    # comp1: A vs B, active, duration 30 (ends soonest) -> visible to C.
    comp1 = market("A", "B", 30)
    # comp2: C vs D, active -> C is the challenger -> excluded.
    comp2 = market("C", "D")
    # comp3: E vs C, active -> C is the opponent -> excluded.
    comp3 = market("E", "C")
    # comp4: A vs D, active, duration 1440 (ends last) -> visible to C.
    comp4 = market("A", "D", 1440)
    # comp6: X vs B, active, but X becomes inactive -> excluded.
    comp6 = market("X", "B")
    # comp7: A vs Y, active, but Y becomes inactive -> excluded.
    comp7 = market("A", "Y")
    deactivate(USERS["X"])
    deactivate(USERS["Y"])

    comp1_id = competition_id_of(comp1)
    comp2_id = competition_id_of(comp2)
    comp3_id = competition_id_of(comp3)
    comp4_id = competition_id_of(comp4)
    comp5_id = competition_id_of(comp5)
    comp6_id = competition_id_of(comp6)
    comp7_id = competition_id_of(comp7)

    # --- 1. Unauthenticated -> 401 -------------------------------------------------------
    r = client.get("/competitions/discover")
    results.append(("unauthenticated discover -> 401", r.status_code == 401, str(r.status_code)))

    # --- 2-10. Discovery feed content ------------------------------------------------------
    r = discover(TOKENS["C"])
    body = r.json()
    item_ids = [i["competition_id"] for i in body["items"]]
    test_ids = {comp1_id, comp2_id, comp3_id, comp4_id, comp5_id, comp6_id, comp7_id}
    visible_test = sorted(i for i in item_ids if i in test_ids)
    ok = (
        r.status_code == 200
        and body["limit"] == 20
        and body["offset"] == 0
        and comp1_id in item_ids and comp4_id in item_ids
        and visible_test == sorted([comp1_id, comp4_id])   # exactly the two others-users comps
        and all(i["status"] == "active" for i in body["items"])
        and all(i["total_votes"] == 0 for i in body["items"])
        and all(
            i["challenger"]["user_id"] != IDS["C"] and i["opponent"]["user_id"] != IDS["C"]
            for i in body["items"]
        )
    )
    results.append(("active competitions between other users are discoverable (received comp1+comp4)", ok, str(item_ids)))
    results.append(("own competition as challenger (C-vs-D) excluded", comp2_id not in item_ids, ""))
    results.append(("own competition as opponent (E-vs-C) excluded", comp3_id not in item_ids, ""))
    results.append(("completed competition excluded", comp5_id not in item_ids, ""))
    results.append(("competition with inactive challenger excluded", comp6_id not in item_ids, ""))
    results.append(("competition with inactive opponent excluded", comp7_id not in item_ids, ""))
    results.append(("competition_id is included in the response", comp1_id in item_ids and comp4_id in item_ids, ""))

    # --- 11-14. Response security ---------------------------------------------------------
    text = str(body)
    user_allowed = {"user_id", "username", "profile_picture_url", "biography"}
    leaks = set(body.keys()) - {"items", "total", "limit", "offset"}
    for item in body["items"]:
        leaks |= set(item.keys()) - {
            "competition_id", "request_id", "challenger", "opponent", "status", "prize_pool",
            "total_votes", "winner_id", "duration_minutes", "start_time", "end_time", "created_at",
        }
        leaks |= set(item["challenger"].keys()) - user_allowed
        leaks |= set(item["opponent"].keys()) - user_allowed
    ok = (
        not leaks
        and "password_hash" not in text
        and "email" not in text
        and "coin_balance" not in text
        and "winning_streak" not in text
        and "is_active" not in text
    )
    results.append(("no sensitive or private fields exposed (public-user shape only)", ok, "" if ok else str(leaks)))

    # Challenger/opponent are the public UserPublic shape on every item.
    ok = all(
        set(i["challenger"].keys()) == user_allowed and set(i["opponent"].keys()) == user_allowed
        for i in body["items"]
    )
    results.append(("items use the intended public-user schema", ok, ""))

    # --- 18-19. Ordering ------------------------------------------------------------------
    from datetime import datetime as _dt
    end_times = [_dt.fromisoformat(i["end_time"].replace("Z", "+00:00")) for i in body["items"]]
    results.append(("deterministic ordering: end_time ascending (ending soonest first)",
                    end_times == sorted(end_times), ""))
    results.append(("shorter-duration competition ends before the longer one (comp1 before comp4)",
                    item_ids.index(comp1_id) < item_ids.index(comp4_id), ""))

    # --- 15-17. Pagination ---------------------------------------------------------------
    full = discover(TOKENS["C"], "?limit=50").json()
    step = 2
    collected = []
    offset = 0
    while True:
        page = discover(TOKENS["C"], f"?limit={step}&offset={offset}").json()
        collected += page["items"]
        if offset + step >= page["total"]:
            break
        offset += step
    ok = (
        len(collected) == full["total"]
        and [i["competition_id"] for i in collected] == [i["competition_id"] for i in full["items"]]
    )
    results.append(("limit/offset pagination tiles the whole feed without overlap/gaps", ok, str(full["total"])))
    r = discover(TOKENS["C"], "?limit=20")
    results.append(("default limit is 20", r.status_code == 200 and r.json()["limit"] == 20, str(r.json()["limit"])))
    r = discover(TOKENS["C"], "?limit=50")
    results.append(("maximum limit 50 accepted", r.status_code == 200 and r.json()["limit"] == 50, str(r.status_code)))
    for q, label in (("?limit=51", "limit>50 -> 422"), ("?limit=0", "limit=0 -> 422"),
                     ("?offset=-1", "negative offset -> 422")):
        r = discover(TOKENS["C"], q)
        results.append((label, r.status_code == 422, str(r.status_code)))

finally:
    cleanup()

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)