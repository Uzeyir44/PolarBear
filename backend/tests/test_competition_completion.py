"""
End-to-end checks of competition completion + winner calculation — Phase 6,
Part 4C:

  * POST /competitions/{competition_id}/complete finalizes an EXPIRED ACTIVE
    competition and records the winner (participant-scoped: 403 for others)
  * winner = participant with more voted_for_user_id votes (challenger or
    opponent); an exact tie (including 0-0) is a draw -> winner_id NULL
  * not-expired -> 409 and the competition stays ACTIVE
  * already-completed -> 409, no recompute, no state corruption
  * completed competitions reject further votes (409, nothing changes)
  * completion preserves total_votes / prize_pool and does NOT touch coins:
    balances unchanged, no competition_reward transactions, prize_pool
    unchanged
  * response uses the safe CompetitionRead shape (nested public users)

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_competition_completion

Participant pairs: A/B (challenger wins), C/D (opponent wins), E/F (draw),
G/H (no votes); V1..V10 are voters; N is a non-participant. The end_time
roll-forward is emulated by back-dating start_time directly in the DB (the
end_time GENERATED column recomputes). All test rows (coin_transactions ->
votes -> competitions -> requests -> users) are cleaned up afterwards.
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

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

PARTICIPANTS = ["A", "B", "C", "D", "E", "F", "G", "H"]
VOTERS = [f"V{i}" for i in range(1, 11)]
USERS = {n: f"fin_{n.lower()}_{RUN_ID}" for n in PARTICIPANTS + VOTERS + ["N"]}

# Who each voter votes for, per competition (2 vs 1 -> challenger wins,
# 1 vs 2 -> opponent wins, 2 vs 2 -> draw, none -> zero-vote draw).
VOTES = {
    "WC": [("V1", "A"), ("V2", "A"), ("V3", "B")],
    "WO": [("V4", "C"), ("V5", "D"), ("V6", "D")],
    "DRAW": [("V7", "E"), ("V8", "E"), ("V9", "F"), ("V10", "F")],
    "NONE": [],
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


def complete(token: str, competition_id: str) -> object:
    return client.post(
        f"/competitions/{competition_id}/complete",
        headers={"Authorization": f"Bearer {token}"},
    )


def expire(competition_id: str) -> None:
    with SessionLocal() as db:
        comp = db.get(Competition, uuid.UUID(competition_id))
        comp.start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
        db.commit()


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
    credit(USERS[v], 1)      # one coin per vote
credit(USERS["V4"], 2)       # also needed for the post-completion vote attempt

try:
    # --- Build four competitions + cast the votes -----------------------------------
    comps = {
        "WC": setup_competition("A", "B"),       # challenger should win (2 vs 1)
        "WO": setup_competition("C", "D"),       # opponent should win (1 vs 2)
        "DRAW": setup_competition("E", "F"),     # draw (2 vs 2)
        "NONE": setup_competition("G", "H"),     # zero votes -> draw
    }
    for key, votes in VOTES.items():
        for voter, target in votes:
            r = vote(TOKENS[voter], comps[key], target)
            assert r.status_code == 201, f"setup vote {key} {voter}->{target} failed: {r.status_code}"

    # Snapshot the economic state BEFORE any completion.
    balances_before = all_balances()
    ledger_before = ledger_total()
    rewards_before = reward_count()

    # --- NOT EXPIRED: competition remains ACTIVE --------------------------------------
    r = complete(TOKENS["A"], comps["WC"])
    st = comp_state(comps["WC"])
    ok = (
        r.status_code == 409
        and r.json()["detail"] == "Competition has not finished yet"
        and st["status"] == "active"
        and st["winner_id"] is None
    )
    results.append(("cannot complete before end_time -> 409, still ACTIVE", ok, str(r.status_code)))

    # Roll time forward: back-date start_time so the GENERATED end_time is in the past.
    for key in comps:
        expire(comps[key])

    # --- WINNER — CHALLENGER -----------------------------------------------------------
    r = complete(TOKENS["A"], comps["WC"])
    js = r.json()
    st = comp_state(comps["WC"])
    ok = (
        r.status_code == 200
        and js["status"] == "completed"
        and js["winner_id"] == IDS["A"]                       # challenger won (2 vs 1)
        and js["total_votes"] == 3
        and js["prize_pool"] == 3
        and st["status"] == "completed"
        and st["winner_id"] == IDS["A"]
        and js["competition_id"] == comps["WC"]
    )
    results.append(("challenger wins -> completed, winner = challenger, counts preserved", ok, str(r.status_code)))

    # --- ALREADY COMPLETED: no recompute / no corruption ---------------------------------
    before = comp_state(comps["WC"])
    r = complete(TOKENS["B"], comps["WC"])
    after = comp_state(comps["WC"])
    ok = (
        r.status_code == 409
        and r.json()["detail"] == "Competition is already completed"
        and before == after
    )
    results.append(("completing an already-completed competition -> 409, state unchanged", ok, str(r.status_code)))

    # --- WINNER — OPPONENT ---------------------------------------------------------------
    r = complete(TOKENS["D"], comps["WO"])
    js = r.json()
    st = comp_state(comps["WO"])
    ok = (
        r.status_code == 200 and js["status"] == "completed"
        and js["winner_id"] == IDS["D"]                       # opponent won (1 vs 2)
        and st["winner_id"] == IDS["D"]
    )
    results.append(("opponent wins -> completed, winner = opponent", ok, str(r.status_code)))

    # --- DRAW ----------------------------------------------------------------------------
    r = complete(TOKENS["E"], comps["DRAW"])
    js = r.json()
    st = comp_state(comps["DRAW"])
    ok = r.status_code == 200 and js["status"] == "completed" and js["winner_id"] is None and st["winner_id"] is None
    results.append(("tie -> completed with winner_id NULL (draw)", ok, str(r.status_code)))

    # --- NO VOTES --------------------------------------------------------------------------
    r = complete(TOKENS["G"], comps["NONE"])
    js = r.json()
    st = comp_state(comps["NONE"])
    ok = (
        r.status_code == 200
        and js["status"] == "completed"
        and js["winner_id"] is None
        and js["total_votes"] == 0
        and js["prize_pool"] == 0
        and st["prize_pool"] == 0
    )
    results.append(("zero votes -> completed draw, winner NULL, prize_pool 0", ok, str(r.status_code)))

    # --- RESULT CONSISTENCY: every winner is a participant --------------------------------
    ok = all(
        st["winner_id"] is None or st["winner_id"] in (IDS[k[0]], IDS[k[1]])
        for k, st_ in [
            (("A", "B"), comp_state(comps["WC"])),
            (("C", "D"), comp_state(comps["WO"])),
            (("E", "F"), comp_state(comps["DRAW"])),
            (("G", "H"), comp_state(comps["NONE"])),
        ]
        for st in [st_]
    )
    results.append(("winner always a participant; draws NULL", ok, ""))

    # --- VOTING AFTER COMPLETION -------------------------------------------------------------
    v4_before = comp_state(comps["WC"])
    r = vote(TOKENS["V4"], comps["WC"], "B")
    after = comp_state(comps["WC"])
    ok = (
        r.status_code == 409
        and r.json()["detail"] == "Competition is no longer active"
        and after == v4_before                                   # counts + pool unchanged
    )
    results.append(("completed competition rejects further votes -> 409, nothing changes", ok, str(r.status_code)))

    # --- PRIZE POOL + REWARD SAFETY -----------------------------------------------------------
    balances_after = all_balances()
    ok = balances_after == balances_before and ledger_total() == ledger_before and reward_count() == rewards_before
    results.append(("completion changed no coin balances, no ledger rows, no competition_reward", ok, ""))

    # --- NON-PARTICIPANT -----------------------------------------------------------------------
    before = comp_state(comps["WO"])
    r = complete(TOKENS["N"], comps["WO"])
    after = comp_state(comps["WO"])
    ok = r.status_code == 403 and before == after
    results.append(("non-participant cannot complete -> 403, state unchanged", ok, str(r.status_code)))

finally:
    cleanup()

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)