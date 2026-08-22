"""
End-to-end checks of competition management & discovery — Phase 6, Part 3:

  * max 3 ACTIVE competitions per user, enforced on acceptance, both
    participants checked, request left PENDING on failure, no competition row
  * completed competitions do not count toward the limit
  * a given unordered pair may have at most one ACTIVE competition (either
    direction); a new one is allowed after the old one is completed
  * GET /competitions is participant-scoped with an optional status filter
    (active|completed), invalid values -> 422, unauthenticated -> 401
  * GET /competitions/{id} remains participant-only (403 for others)
  * concurrency: a user at 2 active who accepts two requests at once ends
    with exactly 3 active — never 4 (serialized via user-row FOR UPDATE locks)

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_competition_management

A = the user pushed to 3 active competitions; B/C/D are opponents; E/F are
fillers; G is the concurrency subject. 'completed' flips are applied directly
in the DB (as other tests manipulate user state directly). All test rows
(competitions -> requests -> users) are cleaned up afterwards.
"""
import threading
import time
import uuid
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient
from sqlalchemy import func, or_, select

from app.core.database import SessionLocal
from app.main import app
from app.models import Competition, CompetitionRequest, CompetitionStatus, User

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

USERS = {n: f"mng_{n.lower()}_{RUN_ID}" for n in ["A", "B", "C", "D", "E", "F", "G"]}


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


def list_comp(token: str, query: str = "") -> object:
    return client.get(f"/competitions{query}", headers={"Authorization": f"Bearer {token}"})


def get_comp(token: str, competition_id: str) -> object:
    return client.get(f"/competitions/{competition_id}", headers={"Authorization": f"Bearer {token}"})


def _status_id(name: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(CompetitionStatus.status_id).where(CompetitionStatus.status_name == name))


def active_count(user_id: str) -> int:
    with SessionLocal() as db:
        return db.scalar(
            select(func.count()).select_from(Competition).where(
                Competition.status_id == _status_id("active"),
                or_(
                    Competition.challenger_id == uuid.UUID(user_id),
                    Competition.opponent_id == uuid.UUID(user_id),
                ),
            )
        )


def competition_id_of(request_id: str) -> str | None:
    with SessionLocal() as db:
        row = db.execute(
            select(Competition.competition_id).where(Competition.request_id == uuid.UUID(request_id))
        ).scalar_one_or_none()
        return str(row) if row else None


def request_status(request_id: str) -> str:
    with SessionLocal() as db:
        return db.get(CompetitionRequest, uuid.UUID(request_id)).status.name


def mark_completed(request_id: str) -> None:
    with SessionLocal() as db:
        comp = db.execute(
            select(Competition).where(Competition.request_id == uuid.UUID(request_id))
        ).scalar_one()
        comp.status_id = _status_id("completed")
        db.commit()


def team(challenger_token: str, opponent_token: str, opponent_id: str) -> tuple:
    req = send_request(challenger_token, opponent_id)
    code = accept(opponent_token, req).status_code
    return code, req


results = []

# --- Setup ---------------------------------------------------------------
for name, username in USERS.items():
    register(username)
TOKENS = {name: login(USERS[name]) for name in USERS}
IDS = {}
with SessionLocal() as db:
    for name in USERS:
        IDS[name] = str(db.execute(select(User.user_id).where(User.username == USERS[name])).scalar_one())

try:
    # --- Phase 1: active-competition limit ---------------------------------------------
    # A reaches 3 active via three distinct opponents (B, C, D accept).
    c1 = send_request(TOKENS["A"], IDS["B"])
    r = accept(TOKENS["B"], c1)
    results.append(("0-active user (B) can accept -> 200", r.status_code == 200, str(r.status_code)))
    results.append(("A now has 1 active", active_count(IDS["A"]) == 1, str(active_count(IDS["A"]))))

    c2 = send_request(TOKENS["A"], IDS["C"])
    r = accept(TOKENS["C"], c2)
    results.append(("1-active user can go to a second -> 200", r.status_code == 200, str(r.status_code)))
    results.append(("A now has 2 active", active_count(IDS["A"]) == 2, str(active_count(IDS["A"]))))

    c3 = send_request(TOKENS["A"], IDS["D"])
    r = accept(TOKENS["D"], c3)
    results.append(("2-active user can go to a third -> 200", r.status_code == 200, str(r.status_code)))
    results.append(("A now has 3 active", active_count(IDS["A"]) == 3, str(active_count(IDS["A"]))))

    # 3-active opponent: E -> A, A (opponent) tries to accept.
    e_to_a = send_request(TOKENS["E"], IDS["A"])
    r = accept(TOKENS["A"], e_to_a)
    ok = (
        r.status_code == 409
        and request_status(e_to_a) == "PENDING"
        and competition_id_of(e_to_a) is None
        and active_count(IDS["A"]) == 3
    )
    results.append(("opponent at 3 active cannot accept -> 409, request PENDING, no comp", ok, str(r.status_code)))

    # 3-active challenger: A -> E, E (opponent) tries to accept.
    a_to_e = send_request(TOKENS["A"], IDS["E"])
    r = accept(TOKENS["E"], a_to_e)
    ok = (
        r.status_code == 409
        and request_status(a_to_e) == "PENDING"
        and competition_id_of(a_to_e) is None
        and active_count(IDS["A"]) == 3
    )
    results.append(("challenger at 3 active blocks creation -> 409, request PENDING, no comp", ok, str(r.status_code)))

    # --- Phase 2: duplicate active matchup ------------------------------------------------
    dup_ab = send_request(TOKENS["A"], IDS["B"])
    r = accept(TOKENS["B"], dup_ab)
    ok = r.status_code == 409 and request_status(dup_ab) == "PENDING" and competition_id_of(dup_ab) is None
    results.append(("A-vs-B already active blocks A->B again -> 409", ok, str(r.status_code)))
    dup_ba = send_request(TOKENS["B"], IDS["A"])
    r = accept(TOKENS["A"], dup_ba)
    ok = r.status_code == 409 and request_status(dup_ba) == "PENDING" and competition_id_of(dup_ba) is None
    results.append(("A-vs-B already active blocks B->A (reverse) -> 409", ok, str(r.status_code)))

    # --- Phase 3: completed competitions don't count; pairs may recompete ------------------
    mark_completed(c1)
    results.append(("after C1 completed, A has 2 active (completed excluded)", active_count(IDS["A"]) == 2, str(active_count(IDS["A"]))))
    # A (now at 2 active) accepts the same opponent again: proves BOTH that the
    # completed competition no longer counts toward the 3-limit AND that the
    # duplicate-matchup rule ignores completed competitions.
    recompete = send_request(TOKENS["A"], IDS["B"])
    r = accept(TOKENS["B"], recompete)
    ok = r.status_code == 200 and active_count(IDS["A"]) == 3 and active_count(IDS["B"]) == 1
    results.append(("A-vs-B can recompete after the old one completed -> 200, A at 3 active", ok, str(r.status_code)))

    c1_comp = competition_id_of(c1)
    c2_comp = competition_id_of(c2)
    c3_comp = competition_id_of(c3)
    recompete_comp = competition_id_of(recompete)

    # --- Phase 4: list / discovery --------------------------------------------------------
    mine = [c1_comp, c2_comp, c3_comp, recompete_comp]
    r = list_comp(TOKENS["A"])
    items = r.json()
    ok = (
        r.status_code == 200
        and len(items) == 4
        and all(i["competition_id"] in mine for i in items)
        and all(
            i["challenger"]["user_id"] == IDS["A"] or i["opponent"]["user_id"] == IDS["A"]
            for i in items
        )
    )
    results.append(("A's list returns exactly A's competitions", ok, str(len(items))))

    r = list_comp(TOKENS["D"])
    items = r.json()
    ok = (
        r.status_code == 200
        and c1_comp not in [i["competition_id"] for i in items]
        and all(
            i["challenger"]["user_id"] == IDS["D"] or i["opponent"]["user_id"] == IDS["D"]
            for i in items
        )
    )
    results.append(("D's list excludes A-vs-B competition (participant-scoped)", ok, str(len(items))))

    r = list_comp(TOKENS["A"], "?status=active")
    items = r.json()
    ok = (
        r.status_code == 200
        and all(i["status"] == "active" for i in items)
        and c1_comp not in [i["competition_id"] for i in items]
        and len(items) == 3
    )
    results.append(("status=active returns only active competitions", ok, str(len(items))))

    r = list_comp(TOKENS["A"], "?status=completed")
    items = r.json()
    ok = (
        r.status_code == 200
        and len(items) == 1
        and items[0]["competition_id"] == c1_comp
        and items[0]["status"] == "completed"
    )
    results.append(("status=completed returns only completed competitions", ok, str(len(items))))

    r = list_comp(TOKENS["A"], "?status=bogus")
    results.append(("invalid status -> 422", r.status_code == 422, str(r.status_code)))
    r = client.get("/competitions")
    results.append(("unauthenticated list -> 401", r.status_code == 401, str(r.status_code)))

    # --- Phase 5: detail remains participant-scoped ------------------------------------------
    results.append(("challenger can retrieve competition -> 200", get_comp(TOKENS["A"], c1_comp).status_code == 200, ""))
    results.append(("opponent can retrieve competition -> 200", get_comp(TOKENS["B"], c1_comp).status_code == 200, ""))
    results.append(("non-participant retrieve -> 403", get_comp(TOKENS["E"], c1_comp).status_code == 403, ""))
    r = client.get(f"/competitions/{c1_comp}")
    results.append(("unauthenticated detail -> 401", r.status_code == 401, str(r.status_code)))

    # --- Phase 6: concurrency — user with 2 active accepts two requests at once --------------
    # G to 2 active via C and D as challengers.
    code1, con1 = team(TOKENS["C"], TOKENS["G"], IDS["G"])
    code2, con2 = team(TOKENS["D"], TOKENS["G"], IDS["G"])
    results.append(("setup: G reaches 2 active", code1 == 200 and code2 == 200 and active_count(IDS["G"]) == 2, f"{active_count(IDS['G'])}"))

    con3 = send_request(TOKENS["E"], IDS["G"])
    con4 = send_request(TOKENS["F"], IDS["G"])
    barrier = threading.Barrier(2)
    outcomes = []

    def race_accept(req: str) -> None:
        barrier.wait()
        outcomes.append(accept(TOKENS["G"], req).status_code)

    threads = [
        threading.Thread(target=race_accept, args=(con3,), name=f"race_g_3"),
        threading.Thread(target=race_accept, args=(con4,), name=f"race_g_4"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = (
        sorted(outcomes) == [200, 409]
        and active_count(IDS["G"]) == 3
        and (competition_id_of(con3) is None) != (competition_id_of(con4) is None)  # exactly one new comp
        and sorted([request_status(con3), request_status(con4)]) == ["ACCEPTED", "PENDING"]
    )
    results.append(("concurrent accepts at 2 active -> one 200/one 409, exactly 3 active total",
                    ok, f"outcomes={outcomes} active={active_count(IDS['G'])}"))

finally:
    cleanup()

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)