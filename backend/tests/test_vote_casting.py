"""
End-to-end checks of basic vote casting — Phase 6, Part 4B-1:

  * a third-party user can vote for either participant (challenger/opponent)
  * self-voting is impossible (challenger and opponent -> 400)
  * voted_for_user_id must be a participant (non-participant target -> 400)
  * only ACTIVE competitions accept votes (completed -> 409)
  * one vote per user per competition (duplicate -> 409 via the UNIQUE
    constraint), and a duplicate never increases total_votes
  * concurrent duplicate votes from one voter -> exactly one 201 + one 409
  * the vote row and the total_votes increment commit atomically
  * THIS STAGE IS FREE: coin_balance is never modified and no
    coin_transactions row is created

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_vote_casting

A = challenger, B = opponent of the competition; C/D/E/F are third-party
voters. The completed flip is applied directly in the DB. All test rows
(votes -> competitions -> requests -> users) are cleaned up afterwards.
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
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.main import app
from app.models import CoinTransaction, Competition, CompetitionRequest, CompetitionStatus, User, Vote

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

USERS = {n: f"vote_{n.lower()}_{RUN_ID}" for n in ["A", "B", "C", "D", "E", "F"]}


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


def total_votes(competition_id: str) -> int:
    with SessionLocal() as db:
        return db.get(Competition, uuid.UUID(competition_id)).total_votes


def vote_count(competition_id: str, voter_id: str) -> int:
    with SessionLocal() as db:
        return len(
            db.execute(
                select(Vote).where(
                    Vote.competition_id == uuid.UUID(competition_id),
                    Vote.voter_id == uuid.UUID(voter_id),
                )
            ).scalars().all()
        )


def coin_balances() -> dict:
    with SessionLocal() as db:
        rows = db.execute(
            select(User.username, User.coin_balance).where(User.username.in_(USERS.values()))
        ).all()
        return {u: b for u, b in rows}


def coin_transaction_count() -> int:
    with SessionLocal() as db:
        user_ids = db.execute(select(User.user_id).where(User.username.in_(USERS.values()))).scalars().all()
        if not user_ids:
            return 0
        return db.scalar(
            select(func.count()).select_from(CoinTransaction).where(
                CoinTransaction.user_id.in_(user_ids)
            )
        )


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

try:
    # Competition A (challenger) vs B (opponent), ACTIVE.
    req = client.post(
        "/competition-requests",
        headers={"Authorization": f"Bearer {TOKENS['A']}"},
        json={"opponent_id": IDS["B"], "duration_minutes": 60},
    ).json()["request_id"]
    r = client.post(
        f"/competition-requests/{req}/accept", headers={"Authorization": f"Bearer {TOKENS['B']}"}
    )
    assert r.status_code == 200, f"setup accept failed {r.status_code}"
    with SessionLocal() as db:
        comp_id = str(db.execute(
            select(Competition.competition_id).where(Competition.request_id == uuid.UUID(req))
        ).scalar_one())
    results.append(("setup competition created and active", total_votes(comp_id) == 0, ""))

    # --- 1,3,5. Third-party C votes for the challenger --------------------------------------
    r = vote(TOKENS["C"], comp_id, IDS["A"])
    js = r.json()
    ok = (
        r.status_code == 201
        and js["vote_id"]
        and js["competition_id"] == comp_id
        and js["voter_id"] == IDS["C"]
        and js["voted_for_user_id"] == IDS["A"]
        and js["total_votes"] == 1
        and js["created_at"] is not None
    )
    results.append(("third-party user can vote for challenger -> 201 + confirmation", ok, str(r.status_code)))
    results.append(("successful vote creates exactly one vote record",
                    vote_count(comp_id, IDS["C"]) == 1 and vote_count(comp_id, IDS["A"]) == 0, ""))

    # --- 2,4. Third-party D votes for the opponent ------------------------------------------
    r = vote(TOKENS["D"], comp_id, IDS["B"])
    ok = r.status_code == 201 and r.json()["total_votes"] == 2 and r.json()["voted_for_user_id"] == IDS["B"]
    results.append(("third-party user can vote for opponent -> 201, total_votes = 2", ok, str(r.status_code)))
    results.append(("total_votes stored +1 per successful vote", total_votes(comp_id) == 2, str(total_votes(comp_id))))

    # --- 14,15. Coin safety (voting is FREE in this stage) ----------------------------------
    balances = coin_balances()
    ok = (
        all(b == 0 for b in balances.values())
        and coin_transaction_count() == 0
    )
    results.append(("votes did NOT modify any user's coin_balance", ok, str(balances)))
    results.append(("votes did NOT create any coin_transactions row", coin_transaction_count() == 0, ""))

    # --- 6. Unauthenticated -> 401 ------------------------------------------------------------
    r = client.post(f"/competitions/{comp_id}/votes", json={"voted_for_user_id": IDS["A"]})
    results.append(("unauthenticated vote -> 401", r.status_code == 401, str(r.status_code)))

    # --- 7,8. Self-voting impossible ------------------------------------------------------------
    r = vote(TOKENS["A"], comp_id, IDS["B"])
    ok = r.status_code == 400 and vote_count(comp_id, IDS["A"]) == 0
    results.append(("challenger cannot vote -> 400, no vote row", ok, str(r.status_code)))
    r = vote(TOKENS["B"], comp_id, IDS["A"])
    ok = r.status_code == 400 and vote_count(comp_id, IDS["B"]) == 0
    results.append(("opponent cannot vote -> 400, no vote row", ok, str(r.status_code)))

    # --- 9. Target must be a participant --------------------------------------------------------
    r = vote(TOKENS["E"], comp_id, IDS["F"])
    ok = r.status_code == 400 and vote_count(comp_id, IDS["E"]) == 0
    results.append(("non-participant target rejected -> 400, no vote row", ok, str(r.status_code)))

    # --- 11,12. One vote per user per competition -----------------------------------------------
    r = vote(TOKENS["C"], comp_id, IDS["B"])
    ok = (
        r.status_code == 409
        and vote_count(comp_id, IDS["C"]) == 1
        and total_votes(comp_id) == 2          # duplicate did not bump the count
        and r.json()["detail"] == "You have already voted in this competition"
    )
    results.append(("duplicate vote -> 409, no new row, total_votes unchanged", ok, str(r.status_code)))

    # --- 13. Concurrency: same voter twice at once -----------------------------------------------
    barrier = threading.Barrier(2)
    outcomes = []

    def race_vote() -> None:
        barrier.wait()
        outcomes.append(vote(TOKENS["E"], comp_id, IDS["A"]).status_code)

    threads = [threading.Thread(target=race_vote, name=f"race_vote_{i}") for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok = (
        sorted(outcomes) == [201, 409]
        and vote_count(comp_id, IDS["E"]) == 1
        and total_votes(comp_id) == 3
    )
    results.append(("concurrent duplicate votes -> one 201/one 409, one row, +1 only", ok, f"outcomes={outcomes}"))

    # --- 10. Completed competitions reject votes ---------------------------------------------------
    mark_completed(comp_id)
    r = vote(TOKENS["F"], comp_id, IDS["A"])
    ok = (
        r.status_code == 409
        and vote_count(comp_id, IDS["F"]) == 0
        and total_votes(comp_id) == 3
    )
    results.append(("vote in completed competition -> 409, no vote row, count unchanged", ok, str(r.status_code)))

finally:
    cleanup()

failed = 0
for name, ok, extra in results:
    report(name, ok, extra)
    failed += 0 if ok else 1
print(f"\n{len(results) - failed}/{len(results)} checks passed.")
if failed:
    raise SystemExit(1)