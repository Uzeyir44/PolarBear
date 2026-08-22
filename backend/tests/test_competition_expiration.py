"""
End-to-end checks of automatic competition expiration — the backend background
mechanism (Phase 6, Part 4C follow-up):

  * sweep_expired_competitions() completes every ACTIVE competition whose
    end_time has passed (winner/draw + status -> completed), using the SAME
    completion service as the manual endpoint
  * a competition that has not reached end_time stays ACTIVE across the sweep
  * winner calculation (challenger wins / opponent wins) and draw (winner NULL)
    are correct during automatic completion
  * an already-completed competition is never processed again (repeated sweeps
    return 0 and change nothing)
  * multiple expired competitions are all finalized in one sweep
  * a completed competition rejects further votes via the API
  * the sweep preserves prize_pool and no user coin balances / no ledger rows /
    no competition_reward transactions are created

The background thread itself is started by FastAPI's lifespan (which the test
TestClient does not trigger); the tests drive the shared sweep function
directly, which is exactly what the loop calls. End-time roll-forward is
emulated by back-dating start_time in the DB (the end_time GENERATED column
recomputes). All test rows are cleaned up afterwards.
"""
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

from app.core.database import SessionLocal
from app.main import app
from app.models import (
    CoinTransaction,
    CoinTransactionType,
    Competition,
    CompetitionRequest,
    CompetitionStatus,
    User,
    Vote,
)
from app.services.competition_expiration import sweep_expired_competitions

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

PARTICIPANTS = ["A", "B", "C", "D", "E", "F", "G", "H"]
VOTERS = [f"V{i}" for i in range(1, 9)]
USERS = {n: f"xpr_{n.lower()}_{RUN_ID}" for n in PARTICIPANTS + VOTERS}

VOTES = {
    "WC": [("V1", "A"), ("V2", "A"), ("V3", "B")],      # 2 vs 1 -> challenger A wins
    "DRAW": [("V4", "C"), ("V5", "C"), ("V6", "D"), ("V7", "D")],  # 2 vs 2 -> draw
    "NONE": [],                                          # 0 vs 0 -> draw
    "UNEXPIRED": [],                                     # stays ACTIVE (end not reached)
}


def report(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({extra})" if extra else ""))


def cleanup() -> None:
    with SessionLocal() as db:
        ids = db.execute(select(User.user_id).where(User.username.in_(USERS.values()))).scalars().all()
        if ids:
            for tx in db.execute(
                select(CoinTransaction).where(CoinTransaction.user_id.in_(ids))
            ).scalars().all():
                db.delete(tx)
            reqs = db.execute(
                select(CompetitionRequest).where(
                    CompetitionRequest.challenger_id.in_(ids)
                    | CompetitionRequest.opponent_id.in_(ids)
                )
            ).scalars().all()
            req_ids = [r.request_id for r in reqs]
            comps = []
            if req_ids:
                comps = db.execute(
                    select(Competition).where(Competition.request_id.in_(req_ids))
                ).scalars().all()
            comp_ids = [c.competition_id for c in comps]
            if comp_ids:
                for v in db.execute(
                    select(Vote).where(Vote.competition_id.in_(comp_ids))
                ).scalars().all():
                    db.delete(v)
                for c in comps:
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


def credit(username: str, amount: int) -> None:
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one()
        user.coin_balance = amount
        db.commit()


def setup_competition(challenger: str, opponent: str) -> str:
    req = client.post(
        "/competition-requests",
        headers={"Authorization": f"Bearer {TOKENS[challenger]}"},
        json={"opponent_id": IDS[opponent], "duration_minutes": 60},
    ).json()["request_id"]
    r = client.post(
        f"/competition-requests/{req}/accept", headers={"Authorization": f"Bearer {TOKENS[opponent]}"}
    )
    assert r.status_code == 200, f"setup accept failed {r.status_code}"
    with SessionLocal() as db:
        return str(db.execute(
            select(Competition.competition_id).where(Competition.request_id == uuid.UUID(req))
        ).scalar_one())


def vote(token: str, competition_id: str, target: str) -> object:
    return client.post(
        f"/competitions/{competition_id}/votes",
        headers={"Authorization": f"Bearer {token}"},
        json={"voted_for_user_id": IDS[target]},
    )


def expire(competition_id: str) -> None:
    with SessionLocal() as db:
        comp = db.get(Competition, uuid.UUID(competition_id))
        comp.start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
        db.commit()


def run_sweep() -> int:
    with SessionLocal() as db:
        return sweep_expired_competitions(db)


def comp_state(competition_id: str) -> dict:
    with SessionLocal() as db:
        comp = db.get(Competition, uuid.UUID(competition_id))
        status_name = db.scalar(
            select(CompetitionStatus.status_name).where(CompetitionStatus.status_id == comp.status_id)
        )
        return {
            "status": status_name,
            "winner_id": str(comp.winner_id) if comp.winner_id else None,
            "total_votes": comp.total_votes,
            "prize_pool": comp.prize_pool,
        }


def all_balances() -> dict:
    with SessionLocal() as db:
        return {
            u: b
            for u, b in db.execute(
                select(User.username, User.coin_balance).where(User.username.in_(USERS.values()))
            ).all()
        }


def ledger_total() -> int:
    with SessionLocal() as db:
        ids = db.execute(select(User.user_id).where(User.username.in_(USERS.values()))).scalars().all()
        if not ids:
            return 0
        return len(db.execute(
            select(CoinTransaction).where(CoinTransaction.user_id.in_(ids))
        ).scalars().all())


def reward_count() -> int:
    with SessionLocal() as db:
        ids = db.execute(select(User.user_id).where(User.username.in_(USERS.values()))).scalars().all()
        if not ids:
            return 0
        return len(
            db.execute(
                select(CoinTransaction)
                .join(CoinTransactionType, CoinTransaction.type_id == CoinTransactionType.type_id)
                .where(
                    CoinTransaction.user_id.in_(ids),
                    CoinTransactionType.type_name == "competition_reward",
                )
            ).scalars().all()
        )


results = []

# --- Setup ---------------------------------------------------------------
for n, username in USERS.items():
    register(username)
TOKENS = {n: login(USERS[n]) for n in USERS}
IDS = {}
with SessionLocal() as db:
    for n in USERS:
        IDS[n] = str(db.execute(select(User.user_id).where(User.username == USERS[n])).scalar_one())

for v in VOTERS:
    credit(USERS[v], 1)

try:
    # --- Build competitions + votes ---------------------------------------------------
    comps = {key: setup_competition(*pair) for key, pair in {
        "WC": ("A", "B"), "DRAW": ("C", "D"), "NONE": ("E", "F"), "UNEXPIRED": ("G", "H"),
    }.items()}
    for key, votes in VOTES.items():
        for voter, target in votes:
            r = vote(TOKENS[voter], comps[key], target)
            assert r.status_code == 201, f"setup vote {key} failed: {r.status_code}"

    # Back-date the end for every competition except UNEXPIRED.
    for key in ("WC", "DRAW", "NONE"):
        expire(comps[key])

    # Snapshot economic state before any sweep.
    balances_before = all_balances()
    ledger_before = ledger_total()
    rewards_before = reward_count()
    pool_wc_before = comp_state(comps["WC"])["prize_pool"]

    # --- 1,3,4,6. FIRST SWEEP: multiple expired competitions are finalized --------
    first_sweep = run_sweep()
    st_wc = comp_state(comps["WC"])
    st_draw = comp_state(comps["DRAW"])
    st_none = comp_state(comps["NONE"])
    ok = (
        first_sweep >= 3                                  # at least our three expired comps
        and st_wc["status"] == "completed" and st_wc["winner_id"] == IDS["A"]   # challenger won
        and st_draw["status"] == "completed" and st_draw["winner_id"] is None    # draw
        and st_none["status"] == "completed" and st_none["winner_id"] is None
        and st_none["prize_pool"] == 0
    )
    results.append(("one sweep auto-completes the expired competitions with correct winners/draws", ok,
                    f"count={first_sweep}"))

    # --- 2. NOT EXPIRED: stays ACTIVE ------------------------------------------------
    st_un = comp_state(comps["UNEXPIRED"])
    ok = st_un["status"] == "active" and st_un["winner_id"] is None
    results.append(("competition that has not reached end_time stays ACTIVE", ok, st_un["status"]))

    # --- 7. Completed competition rejects votes via the API ----------------------------
    r = vote(TOKENS["V8"], comps["WC"], "B")
    st_after = comp_state(comps["WC"])
    ok = (
        r.status_code == 409
        and st_after == st_wc                                   # nothing changed
    )
    results.append(("auto-completed competition rejects further votes -> 409", ok, str(r.status_code)))

    # --- 5. ALREADY COMPLETED: repeated sweep never reprocesses ----------------------
    second_sweep = run_sweep()
    ok = (
        second_sweep == 0
        and comp_state(comps["WC"]) == st_wc                     # winner not recomputed
        and comp_state(comps["DRAW"]) == comp_state(comps["DRAW"])
    )
    results.append(("repeated sweep returns 0 and never reprocesses completed competitions", ok,
                    f"count={second_sweep}"))

    # --- 8,9. PRIZE POOL + REWARD SAFETY ----------------------------------------------
    ok = (
        pool_wc_before == 3 and comp_state(comps["WC"])["prize_pool"] == 3   # sweep preserved the pool
        and all_balances() == balances_before
        and ledger_total() == ledger_before
        and reward_count() == rewards_before
    )
    results.append(("sweep preserves prize_pool, no balance/ledger/reward changes", ok, ""))

finally:
    cleanup()

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)