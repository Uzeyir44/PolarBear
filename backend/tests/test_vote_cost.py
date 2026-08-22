"""
End-to-end checks of vote cost + coin transaction — Phase 6, Part 4B-2:

  * every successful vote costs exactly 1 coin (fixed business rule)
  * balance_after reflects the post-vote balance and matches users.coin_balance
  * each vote writes exactly one vote_cast DEBIT coin_transactions row linked
    to the correct user, vote, and competition
  * insufficient balance (0 coins) is rejected with NO vote, NO transaction,
    NO total_votes change, NO balance change
  * duplicate votes never spend a second coin
  * self-votes / invalid targets / completed competitions / unauthenticated
    requests never spend a coin
  * atomicity: if the transaction-type lookup fails mid-flight, the vote +
    spent coin + vote count all roll back (a user's balance is restored)
  * concurrency: with 1 coin and two simultaneous valid votes, at most one
    succeeds, balance reaches 0, exactly one vote and one transaction exist

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_vote_cost

A/B = competition 1 pair, G/H = competition 2 pair; C/D/E/F/I are voters.
Users are credited starting balances directly in the DB (as other tests do).
All test rows (coin_transactions -> votes -> competitions -> requests ->
users, in FK order) are cleaned up afterwards.
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

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

NAMES = ["A", "B", "C", "D", "E", "F", "G", "H", "I"]
USERS = {n: f"cost_{n.lower()}_{RUN_ID}" for n in NAMES}

VOTE_COST = 1


def report(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({extra})" if extra else ""))


def cleanup() -> None:
    with SessionLocal() as db:
        ids = db.execute(select(User.user_id).where(User.username.in_(USERS.values()))).scalars().all()
        reqs = []
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


def vote(token: str, competition_id: str, voted_for_id: str) -> object:
    return client.post(
        f"/competitions/{competition_id}/votes",
        headers={"Authorization": f"Bearer {token}"},
        json={"voted_for_user_id": voted_for_id},
    )


def balance_of(username: str) -> int:
    with SessionLocal() as db:
        return db.execute(
            select(User.coin_balance).where(User.username == username)
        ).scalar_one()


def total_votes(competition_id: str) -> int:
    with SessionLocal() as db:
        return db.get(Competition, uuid.UUID(competition_id)).total_votes


def vote_rows(voter_id: str, competition_id: str) -> int:
    with SessionLocal() as db:
        return len(
            db.execute(
                select(Vote).where(
                    Vote.voter_id == uuid.UUID(voter_id),
                    Vote.competition_id == uuid.UUID(competition_id),
                )
            ).scalars().all()
        )


def tx_rows(voter_id: str) -> list:
    with SessionLocal() as db:
        return db.execute(
            select(CoinTransaction)
            .where(CoinTransaction.user_id == uuid.UUID(voter_id))
            .options(selectinload(CoinTransaction.type))
        ).scalars().all()


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


def mark_completed(competition_id: str) -> None:
    with SessionLocal() as db:
        comp = db.get(Competition, uuid.UUID(competition_id))
        comp.status_id = db.scalar(
            select(CompetitionStatus.status_id).where(CompetitionStatus.status_name == "completed")
        )
        db.commit()


results = []

# --- Setup ---------------------------------------------------------------
for n, username in USERS.items():
    register(username)
TOKENS = {n: login(USERS[n]) for n in USERS}
IDS = {}
with SessionLocal() as db:
    for n in USERS:
        IDS[n] = str(db.execute(select(User.user_id).where(User.username == USERS[n])).scalar_one())

credit(USERS["C"], 1)   # exactly 1 coin
credit(USERS["D"], 5)   # more than 1 coin
credit(USERS["E"], 1)   # the 1-coin concurrency subject
credit(USERS["F"], 3)   # invalid-target / completed-comp / unauth-guard subject
credit(USERS["I"], 3)   # atomicity subject
credit(USERS["A"], 3)   # participant (self-vote guard)
credit(USERS["B"], 3)   # participant (self-vote guard)

comp1 = setup_competition("A", "B")      # ACTIVE
comp2 = setup_competition("G", "H")      # ACTIVE (later completed)

try:
    # --- 1-11. SUCCESS: each vote costs 1 coin and writes a vote_cast DEBIT ledger row ----
    r = vote(TOKENS["C"], comp1, IDS["A"])
    js = r.json()
    ok = (
        r.status_code == 201
        and js["voted_for_user_id"] == IDS["A"]
        and js["balance_after"] == 0            # 1 coin, 1->0
        and js["total_votes"] == 1
    )
    results.append(("user with exactly 1 coin can vote -> 201", ok, str(r.status_code)))
    results.append(("successful vote deducts exactly 1 coin (C 1 -> 0)", balance_of(USERS["C"]) == 0, str(balance_of(USERS["C"]))))
    c_vote_id = js["vote_id"]

    r = vote(TOKENS["D"], comp1, IDS["B"])
    js = r.json()
    ok = (
        r.status_code == 201
        and js["voted_for_user_id"] == IDS["B"]
        and js["balance_after"] == 4            # 5 -> 4
        and js["total_votes"] == 2
    )
    results.append(("user with more than 1 coin can vote -> 201", ok, str(r.status_code)))
    results.append(("successful vote deducts exactly 1 coin (D 5 -> 4)", balance_of(USERS["D"]) == 4, str(balance_of(USERS["D"]))))
    d_vote_id = js["vote_id"]
    results.append(("competition vote count increases by exactly 1 per vote", total_votes(comp1) == 2, str(total_votes(comp1))))

    # Ledger rows for C and D.
    c_tx = tx_rows(IDS["C"])
    d_tx = tx_rows(IDS["D"])
    results.append(("each vote creates exactly one coin_transactions row", len(c_tx) == 1 and len(d_tx) == 1, f"{len(c_tx)}/{len(d_tx)}"))
    ok = (
        c_tx[0].type.type_name == "vote_cast"
        and c_tx[0].type.direction.name == "DEBIT"
        and d_tx[0].type.type_name == "vote_cast"
        and d_tx[0].type.direction.name == "DEBIT"
    )
    results.append(("transaction uses the seeded vote_cast DEBIT type", ok, ""))
    results.append(("transaction amount is -1 (DEBIT sign convention)", c_tx[0].amount == -1 and d_tx[0].amount == -1, f"{c_tx[0].amount}"))
    results.append(("transaction is linked to the correct user", str(c_tx[0].user_id) == IDS["C"] and str(d_tx[0].user_id) == IDS["D"], ""))
    results.append(("transaction is linked to the correct vote", str(c_tx[0].vote_id) == c_vote_id and str(d_tx[0].vote_id) == d_vote_id, ""))
    results.append(("transaction is linked to the correct competition", str(c_tx[0].competition_id) == comp1 and str(d_tx[0].competition_id) == comp1, ""))
    results.append(("balance_after equals the user's post-vote balance",
                    c_tx[0].balance_after == balance_of(USERS["C"]) == 0
                    and d_tx[0].balance_after == balance_of(USERS["D"]) == 4, f"{c_tx[0].balance_after}/{d_tx[0].balance_after}"))

    # --- 18-22. DUPLICATE: the second vote costs nothing --------------------------------------
    before = balance_of(USERS["D"])
    r = vote(TOKENS["D"], comp1, IDS["A"])
    ok = (
        r.status_code == 409
        and r.json()["detail"] == "You have already voted in this competition"
        and balance_of(USERS["D"]) == before          # 4 -> still 4
        and len(tx_rows(IDS["D"])) == 1               # no second transaction
        and total_votes(comp1) == 2                   # count unchanged
        and vote_rows(IDS["D"], comp1) == 1
    )
    results.append(("duplicate vote -> 409, no second coin spent", ok, str(r.status_code)))

    # --- 12-17. INSUFFICIENT BALANCE -----------------------------------------------------------
    c_before = balance_of(USERS["C"])                     # 0
    comp2_total_before = total_votes(comp2)               # 0
    r = vote(TOKENS["C"], comp2, IDS["G"])
    ok = (
        r.status_code == 400
        and r.json()["detail"] == "Not enough coins to vote"
        and vote_rows(IDS["C"], comp2) == 0               # no vote
        and len(tx_rows(IDS["C"])) == 1                   # still only the first vote's tx
        and total_votes(comp2) == comp2_total_before      # count unchanged
        and balance_of(USERS["C"]) == c_before            # balance unchanged (0)
    )
    results.append(("0-coin user cannot vote -> 400 with nothing changed", ok, str(r.status_code)))

    # --- 29. CONCURRENCY: a single coin cannot be spent twice in one instant -------------------
    # E has exactly 1 coin. Two simultaneous valid votes (different competitions,
    # so both pass the one-vote-per-competition rule) race for that coin.
    outcomes = []
    barrier = threading.Barrier(2)

    def vote_in_comp1() -> None:
        barrier.wait()
        outcomes.append(vote(TOKENS["E"], comp1, IDS["A"]).status_code)

    def vote_in_comp2() -> None:
        barrier.wait()
        outcomes.append(vote(TOKENS["E"], comp2, IDS["G"]).status_code)

    threads = [threading.Thread(target=vote_in_comp1, name="c1"), threading.Thread(target=vote_in_comp2, name="c2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = (
        sorted(outcomes) == [201, 400]                     # exactly one vote wins the last coin
        and balance_of(USERS["E"]) == 0                    # the single coin is spent once
        and (vote_rows(IDS["E"], comp1) + vote_rows(IDS["E"], comp2)) == 1
        and len(tx_rows(IDS["E"])) == 1                    # exactly one vote_cast transaction
    )
    results.append(("concurrent votes with 1 coin -> one 201/one 400, one vote, one tx", ok, f"outcomes={outcomes}"))

    # --- 23. Self-vote never spends coins ------------------------------------------------------------
    a_before = balance_of(USERS["A"])
    r = vote(TOKENS["A"], comp1, IDS["B"])
    ok = r.status_code == 400 and balance_of(USERS["A"]) == a_before and len(tx_rows(IDS["A"])) == 0 and vote_rows(IDS["A"], comp1) == 0
    results.append(("challenger self-vote -> 400, no coin spent", ok, str(r.status_code)))
    b_before = balance_of(USERS["B"])
    r = vote(TOKENS["B"], comp1, IDS["A"])
    ok = r.status_code == 400 and balance_of(USERS["B"]) == b_before and len(tx_rows(IDS["B"])) == 0
    results.append(("opponent self-vote -> 400, no coin spent", ok, str(r.status_code)))

    # --- 24. Invalid target never spends coins ---------------------------------------------------------
    f_before = balance_of(USERS["F"])
    r = vote(TOKENS["F"], comp1, IDS["E"])                 # E is not a participant of comp1
    ok = (
        r.status_code == 400
        and r.json()["detail"] == "You can only vote for a participant of this competition"
        and balance_of(USERS["F"]) == f_before
        and len(tx_rows(IDS["F"])) == 0
        and vote_rows(IDS["F"], comp1) == 0
    )
    results.append(("invalid target -> 400, no coin spent", ok, str(r.status_code)))

    # --- 25. Completed competition never spends coins -------------------------------------------------
    mark_completed(comp2)
    f_before = balance_of(USERS["F"])
    comp2_total_before = total_votes(comp2)
    r = vote(TOKENS["F"], comp2, IDS["G"])
    ok = (
        r.status_code == 409
        and r.json()["detail"] == "Competition is no longer active"
        and balance_of(USERS["F"]) == f_before
        and len(tx_rows(IDS["F"])) == 0
        and vote_rows(IDS["F"], comp2) == 0
        and total_votes(comp2) == comp2_total_before
    )
    results.append(("completed competition -> 409, no coin spent", ok, str(r.status_code)))

    # --- 26. Unauthenticated never spends coins -------------------------------------------------------
    d_before = balance_of(USERS["D"])
    r = client.post(f"/competitions/{comp1}/votes", json={"voted_for_user_id": IDS["A"]})
    ok = r.status_code == 401 and balance_of(USERS["D"]) == d_before
    results.append(("unauthenticated vote -> 401, no coin spent", ok, str(r.status_code)))

    # --- 27. ATOMICITY: a mid-transaction failure rolls back vote + spent coin + count --------------
    comp1_total_before = total_votes(comp1)
    i_before = balance_of(USERS["I"])
    with SessionLocal() as db:
        db.execute(
            update(CoinTransactionType)
            .where(CoinTransactionType.type_name == "vote_cast")
            .values(type_name="vote_cast_tmp")
        )
        db.commit()
    r = vote(TOKENS["I"], comp1, IDS["B"])
    with SessionLocal() as db:
        db.execute(
            update(CoinTransactionType)
            .where(CoinTransactionType.type_name == "vote_cast_tmp")
            .values(type_name="vote_cast")
        )
        db.commit()
    ok = (
        r.status_code == 500
        and balance_of(USERS["I"]) == i_before            # the in-transaction 3->2 deduction rolled back
        and vote_rows(IDS["I"], comp1) == 0               # no committed vote
        and len(tx_rows(IDS["I"])) == 0                   # no ledger row
        and total_votes(comp1) == comp1_total_before      # count unchanged
    )
    results.append(("ledger-type failure rolls back vote + deduction + count (atomicity)", ok, str(r.status_code)))

finally:
    cleanup()

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)