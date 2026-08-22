"""
End-to-end checks of competition prize distribution — Phase 6, Part 4D:

  * distribution happens automatically as part of completion (both the sweep
    and the manual endpoint share the same service)
  * a winner receives 100% of the prize pool (one competition_reward CREDIT
    transaction, positive amount, balance_after = post-reward balance)
  * a draw splits the pool equally (two competition_reward transactions)
  * a zero-vote draw (prize_pool = 0) distributes nothing
  * double payout is impossible: repeated sweeps / manual re-completion pay out
    exactly once (no duplicate transactions, no doubled balances)
  * concurrent completion attempts pay out exactly once
  * an atomic failure mid-payout leaves the competition ACTIVE with no partial
    balance change, no partial transactions, and no winner
  * every distributed coin has a corresponding competition_reward CREDIT row

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_prize_distribution

A/B .. K/L are participants: A wins WC (pool 3), D wins WO (pool 3), E/F draw
(pool 4), G/H zero-vote (pool 0), I/J concurrency (pool 2), K/L atomicity
(pool 2). V1..V14 are one-coin voters. All test rows are cleaned up afterwards.
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

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

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
from app.services.competition_expiration import (
    CompletionOutcome,
    complete_expired_competition,
    sweep_expired_competitions,
)

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

PARTICIPANTS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"]
VOTERS = [f"V{i}" for i in range(1, 15)]
USERS = {n: f"prz_{n.lower()}_{RUN_ID}" for n in PARTICIPANTS + VOTERS}

VOTES = {
    "WC": [("V1", "A"), ("V2", "A"), ("V3", "B")],        # 2 vs 1 -> A wins, pool 3
    "WO": [("V4", "C"), ("V5", "D"), ("V6", "D")],        # 1 vs 2 -> D wins, pool 3
    "DRAW": [("V7", "E"), ("V8", "E"), ("V9", "F"), ("V10", "F")],  # draw, pool 4
    "ZERO": [],                                            # zero votes, pool 0
    "CC": [("V11", "I"), ("V12", "I")],                    # I wins, pool 2
    "AF": [("V13", "K"), ("V14", "K")],                    # K would win, pool 2
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


def complete(token: str, competition_id: str) -> object:
    return client.post(
        f"/competitions/{competition_id}/complete",
        headers={"Authorization": f"Bearer {token}"},
    )


def balance_of(username: str) -> int:
    with SessionLocal() as db:
        return db.execute(select(User.coin_balance).where(User.username == username)).scalar_one()


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


def reward_txs(competition_id: str) -> list:
    with SessionLocal() as db:
        return db.execute(
            select(CoinTransaction)
            .where(
                CoinTransaction.competition_id == uuid.UUID(competition_id),
                CoinTransaction.type_id == CoinTransactionType.type_id,
            )
            .join(CoinTransactionType, CoinTransaction.type_id == CoinTransactionType.type_id)
            .where(CoinTransactionType.type_name == "competition_reward")
            .options(selectinload(CoinTransaction.type))
        ).scalars().all()


def reward_txs_for(user_id: str) -> int:
    with SessionLocal() as db:
        return len(
            db.execute(
                select(CoinTransaction)
                .join(CoinTransactionType, CoinTransaction.type_id == CoinTransactionType.type_id)
                .where(
                    CoinTransaction.user_id == uuid.UUID(user_id),
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
    comps = {
        key: setup_competition(*pair)
        for key, pair in {
            "WC": ("A", "B"), "WO": ("C", "D"), "DRAW": ("E", "F"),
            "ZERO": ("G", "H"), "CC": ("I", "J"), "AF": ("K", "L"),
        }.items()
    }
    for key, votes in VOTES.items():
        for voter, target in votes:
            r = vote(TOKENS[voter], comps[key], target)
            assert r.status_code == 201, f"setup vote {key} failed: {r.status_code}"

    # --- ATOMIC FAILURE: a mid-payout error leaves the competition ACTIVE & unpayable --
    expire(comps["AF"])
    with SessionLocal() as db:
        db.execute(
            update(CoinTransactionType)
            .where(CoinTransactionType.type_name == "competition_reward")
            .values(type_name="competition_reward_tmp")
        )
        db.commit()
    http_status = None
    with SessionLocal() as db:
        try:
            complete_expired_competition(db, comps["AF"])
        except HTTPException as exc:
            db.rollback()
            http_status = exc.status_code
    with SessionLocal() as db:
        db.execute(
            update(CoinTransactionType)
            .where(CoinTransactionType.type_name == "competition_reward_tmp")
            .values(type_name="competition_reward")
        )
        db.commit()
    st_af = comp_state(comps["AF"])
    ok = (
        http_status == 500
        and st_af["status"] == "active"          # not committed to completed
        and st_af["winner_id"] is None
        and balance_of(USERS["K"]) == 0          # no partial credit
        and balance_of(USERS["L"]) == 0
        and reward_txs_for(IDS["K"]) == 0        # no partial ledger row
    )
    results.append(("payout failure rolls back completion + all payout changes (atomicity)", ok, f"http={http_status}"))

    # --- CONCURRENT PAYOUT: exactly one completion pays out ------------------------------
    expire(comps["CC"])
    barrier = threading.Barrier(2)
    outcomes = []

    def race_complete(actor: str) -> None:
        barrier.wait()
        outcomes.append(complete(TOKENS[actor], comps["CC"]).status_code)

    threads = [
        threading.Thread(target=race_complete, args=("I",), name="cc_i"),
        threading.Thread(target=race_complete, args=("J",), name="cc_j"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok = (
        sorted(outcomes) == [200, 409]
        and balance_of(USERS["I"]) == 2          # paid exactly once
        and balance_of(USERS["J"]) == 0
        and len(reward_txs(comps["CC"])) == 1
        and comp_state(comps["CC"])["winner_id"] == IDS["I"]
    )
    results.append(("concurrent completions pay the prize exactly once", ok, f"outcomes={outcomes}"))

    # --- AUTOMATIC SWEEP: completes the remaining expired comps + pays out ---------------
    for key in ("WC", "WO", "DRAW", "ZERO"):
        expire(comps[key])
    swept = None
    with SessionLocal() as db:
        swept = sweep_expired_competitions(db)
    ok = swept >= 5   # WC, WO, DRAW, ZERO (+ AF was already expired)
    results.append(("automatic sweep completes + pays out the remaining expired competitions", ok, f"count={swept}"))

    # --- WINNER: challenger won, receives 100% of the pool ---------------------------------
    st = comp_state(comps["WC"])
    txs = reward_txs(comps["WC"])
    ok = (
        st["status"] == "completed" and st["winner_id"] == IDS["A"]
        and balance_of(USERS["A"]) == 3 and balance_of(USERS["B"]) == 0
        and len(txs) == 1
        and str(txs[0].user_id) == IDS["A"]
        and txs[0].amount == 3
        and txs[0].balance_after == 3 and txs[0].balance_after == balance_of(USERS["A"])
        and str(txs[0].competition_id) == comps["WC"]
        and txs[0].type.direction.name == "CREDIT"
    )
    results.append(("winner receives the full prize pool via one CREDIT reward transaction", ok,
                    f"amount={txs[0].amount if txs else '?'}"))

    # --- OPPONENT winner --------------------------------------------------------------
    ok = (
        comp_state(comps["WO"])["winner_id"] == IDS["D"]
        and balance_of(USERS["D"]) == 3 and balance_of(USERS["C"]) == 0
        and len(reward_txs(comps["WO"])) == 1
        and reward_txs(comps["WO"])[0].amount == 3
    )
    results.append(("opponent winner receives the full prize pool", ok, ""))

    # --- DRAW: split equally -----------------------------------------------------------
    txs = reward_txs(comps["DRAW"])
    ok = (
        comp_state(comps["DRAW"])["winner_id"] is None
        and balance_of(USERS["E"]) == 2 and balance_of(USERS["F"]) == 2
        and len(txs) == 2
        and sorted([t.amount for t in txs]) == [2, 2]
        and sorted([str(t.user_id) for t in txs]) == sorted([IDS["E"], IDS["F"]])
        and sum(t.amount for t in txs) == comp_state(comps["DRAW"])["prize_pool"] == 4
    )
    results.append(("draw splits the pool equally into two reward transactions", ok,
                    f"amounts={[t.amount for t in txs]}"))

    # --- ZERO-VOTE DRAW: nothing distributed --------------------------------------------
    ok = (
        comp_state(comps["ZERO"])["winner_id"] is None
        and comp_state(comps["ZERO"])["prize_pool"] == 0
        and balance_of(USERS["G"]) == 0 and balance_of(USERS["H"]) == 0
        and reward_txs_for(IDS["G"]) == 0 and reward_txs_for(IDS["H"]) == 0
        and len(reward_txs(comps["ZERO"])) == 0
    )
    results.append(("zero-vote draw distributes nothing and writes no reward transactions", ok, ""))

    # --- LEDGER: every awarded coin has a matching row --------------------------------
    tx = reward_txs(comps["AF"])[0]
    ok = (
        balance_of(USERS["K"]) == 2 and balance_of(USERS["L"]) == 0
        and len(reward_txs(comps["AF"])) == 1 and tx.amount == 2
        and all(t.amount > 0 for t in reward_txs(comps["WC"]) + reward_txs(comps["WO"])
                + reward_txs(comps["DRAW"]) + reward_txs(comps["CC"]) + reward_txs(comps["AF"]))
    )
    results.append(("atomicity-failed competition completes on the sweep and still pays exactly once", ok, ""))

    # --- DOUBLE PAYOUT: repeated sweep / manual re-completion pay nothing ----------------
    balances_before = {u: balance_of(u) for u in (USERS["A"], USERS["D"], USERS["E"], USERS["F"], USERS["I"], USERS["K"])}
    counts_before = len(reward_txs(comps["WC"])) + len(reward_txs(comps["WO"])) + len(reward_txs(comps["DRAW"])) + len(reward_txs(comps["CC"])) + len(reward_txs(comps["AF"]))
    with SessionLocal() as db:
        outcome, _ = complete_expired_competition(db, comps["WC"])
        second = sweep_expired_competitions(db)
    r = complete(TOKENS["A"], comps["WC"])
    balances_after = {u: balance_of(u) for u in balances_before}
    counts_after = len(reward_txs(comps["WC"])) + len(reward_txs(comps["WO"])) + len(reward_txs(comps["DRAW"])) + len(reward_txs(comps["CC"])) + len(reward_txs(comps["AF"]))
    ok = (
        outcome == CompletionOutcome.ALREADY_COMPLETED
        and second == 0
        and r.status_code == 409
        and balances_after == balances_before
        and counts_after == counts_before
    )
    results.append(("double payout is impossible (re-sweep/manual re-completion change nothing)", ok,
                    f"rewards={counts_after}"))

finally:
    cleanup()

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)