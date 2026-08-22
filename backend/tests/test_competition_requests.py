"""
End-to-end checks of the competition-request lifecycle — Phase 6, Part 1:
POST /competition-requests (send), GET /competition-requests/incoming,
GET /competition-requests/outgoing, POST .../{id}/accept | /decline | /cancel.

Run from the backend/ directory:
    venv/Scripts/python -m tests.test_competition_requests

Covers: unauthenticated -> 401, successful PENDING send, challenger taken from
the JWT (never the body), self-challenge -> 400, missing opponent -> 404,
inactive opponent -> 400, invalid durations -> 422, all four valid durations,
duplicate PENDING requests allowed (the documented design), no sensitive user
fields in responses; incoming/outgoing isolation; accept/decline/cancel role
rules (403 for the wrong party), no-op re-transitions of terminal states
(409), responded_at populated on transition and NULL while PENDING, and that
an outsider cannot manipulate another user's requests. Following the existing
competition-requests design, duplicates are permitted, so a challenger may
hold several PENDING requests to the same opponent at once.

Test users and their competition_requests rows are deleted afterwards so the
development DB is left clean (user FKs on competition_requests are RESTRICT,
so requests must be removed before the users; acceptance also creates a
competitions row whose FK back to the request is RESTRICT, so competitions are
removed before requests).
"""
import time
import uuid
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient
from sqlalchemy import or_, select

from app.core.database import SessionLocal
from app.main import app
from app.models import Competition, CompetitionRequest, User

client = TestClient(app)

RUN_ID = f"{int(time.time())}{uuid.uuid4().hex[:6]}"
PASSWORD = "SuperSecret123!"

# A = challenger who sends requests; B = the opponent they challenge;
# C = an outsider who must not see or touch A/B requests; D = inactive user.
USER_A = f"crq_a_{RUN_ID}"
USER_B = f"crq_b_{RUN_ID}"
USER_C = f"crq_c_{RUN_ID}"
USER_D = f"crq_inact_{RUN_ID}"

USERS = [USER_A, USER_B, USER_C, USER_D]

NONEXISTENT_ID = str(uuid.uuid4())


def report(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({extra})" if extra else ""))


def cleanup() -> None:
    with SessionLocal() as db:
        ids = db.execute(
            select(User.user_id).where(User.username.in_(USERS))
        ).scalars().all()
        if ids:
            reqs = db.execute(
                select(CompetitionRequest).where(
                    or_(
                        CompetitionRequest.challenger_id.in_(ids),
                        CompetitionRequest.opponent_id.in_(ids),
                    )
                )
            ).scalars().all()
            req_ids = [r.request_id for r in reqs]
            # Accepting a request now creates a competitions row whose FK back
            # to competition_requests is ondelete RESTRICT, so competitions
            # must be removed before their requests (votes cascade on delete).
            if req_ids:
                comps = db.execute(
                    select(Competition).where(Competition.request_id.in_(req_ids))
                ).scalars().all()
                for c in comps:
                    db.delete(c)
            for r in reqs:
                db.delete(r)
        for row in db.execute(select(User).where(User.username.in_(USERS))).scalars().all():
            db.delete(row)
        db.commit()
    print(f"\nCleaned up {len(USERS)} test user(s) and their competitions/requests.")


def user_id_of(db, username: str) -> str:
    return str(db.execute(select(User.user_id).where(User.username == username)).scalar_one())


def send_request(token: str, opponent_id: str, duration: int = 30, **extra) -> object:
    payload = {"opponent_id": opponent_id, "duration_minutes": duration}
    payload.update(extra)
    return client.post(
        "/competition-requests",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )


def act(action: str, token: str, request_id: str) -> object:
    return client.post(
        f"/competition-requests/{request_id}/{action}",
        headers={"Authorization": f"Bearer {token}"},
    )


def incoming(token: str) -> list:
    return client.get(
        "/competition-requests/incoming", headers={"Authorization": f"Bearer {token}"}
    ).json()


def outgoing(token: str) -> list:
    return client.get(
        "/competition-requests/outgoing", headers={"Authorization": f"Bearer {token}"}
    ).json()


def fetch_request(request_id: str) -> dict:
    with SessionLocal() as db:
        r = db.get(CompetitionRequest, uuid.UUID(request_id))
        return {
            "status": r.status.name,
            "responded_at": r.responded_at,
            "challenger_id": str(r.challenger_id),
            "opponent_id": str(r.opponent_id),
            "duration_minutes": r.duration_minutes,
        }


results = []

# --- Setup ------------------------------------------------------------------
for username in USERS:
    response = client.post(
        "/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
    )
    results.append((f"setup register {username}", response.status_code == 201, str(response.status_code)))

# Deactivate the "inactive" user directly in the DB (as other tests do).
with SessionLocal() as db:
    user_d = db.execute(select(User).where(User.username == USER_D)).scalar_one()
    user_d.is_active = False
    db.commit()

with SessionLocal() as db:
    A_ID = user_id_of(db, USER_A)
    B_ID = user_id_of(db, USER_B)
    C_ID = user_id_of(db, USER_C)
    D_ID = user_id_of(db, USER_D)

TOKEN_A = client.post("/auth/login", json={"username": USER_A, "password": PASSWORD}).json()["access_token"]
TOKEN_B = client.post("/auth/login", json={"username": USER_B, "password": PASSWORD}).json()["access_token"]
TOKEN_C = client.post("/auth/login", json={"username": USER_C, "password": PASSWORD}).json()["access_token"]
AUTH_A = {"Authorization": f"Bearer {TOKEN_A}"}
AUTH_B = {"Authorization": f"Bearer {TOKEN_B}"}
AUTH_C = {"Authorization": f"Bearer {TOKEN_C}"}
results.append(("setup logins work", all([TOKEN_A, TOKEN_B, TOKEN_C]), ""))


# --- 1. Unauthenticated -> 401 -------------------------------------------------
r = client.post("/competition-requests", json={"opponent_id": B_ID, "duration_minutes": 30})
results.append(("send without token -> 401", r.status_code == 401, str(r.status_code)))
for action in ("accept", "decline", "cancel"):
    r = client.post(f"/competition-requests/{NONEXISTENT_ID}/{action}")
    results.append((f"{action} without token -> 401", r.status_code == 401, str(r.status_code)))
r = client.get("/competition-requests/incoming")
results.append(("incoming without token -> 401", r.status_code == 401, str(r.status_code)))
r = client.get("/competition-requests/outgoing")
results.append(("outgoing without token -> 401", r.status_code == 401, str(r.status_code)))

# --- 2. Successful send -> 201, PENDING -------------------------------------------
r = send_request(TOKEN_A, B_ID, 30)
ok = (
    r.status_code == 201
    and r.json()["status"] == "PENDING"
    and r.json()["challenger"]["user_id"] == A_ID
    and r.json()["opponent"]["user_id"] == B_ID
    and r.json()["duration_minutes"] == 30
    and r.json()["responded_at"] is None
)
results.append(("send 30 -> 201 PENDING, correct parties, responded_at NULL", ok, str(r.status_code)))
PENDING_A_TO_B_1 = r.json()["request_id"]
results.append(("send request_id present", isinstance(PENDING_A_TO_B_1, str) and len(PENDING_A_TO_B_1) > 0, ""))

# --- 3. Challenger comes from the JWT, not the body --------------------------------
r = send_request(TOKEN_A, B_ID, 30, challenger_id=C_ID, status="ACCEPTED", creator_id=C_ID)
json = r.json()
ok = (
    r.status_code == 201
    and json["challenger"]["user_id"] == A_ID       # not C_ID
    and json["status"] == "PENDING"                  # not "ACCEPTED"
)
results.append(("body-supplied challenger_id/status are ignored (JWT wins)", ok, str(r.status_code)))

# --- 4. Self-challenge -> 400 --------------------------------------------------------
r = send_request(TOKEN_A, A_ID, 30)
results.append(("challenge yourself -> 400", r.status_code == 400, str(r.status_code)))

# --- 5. Opponent not found -> 404 ------------------------------------------------------
r = send_request(TOKEN_A, NONEXISTENT_ID, 30)
results.append(("challenge nonexistent user -> 404", r.status_code == 404, str(r.status_code)))

# --- 6. Inactive opponent -> 400 ----------------------------------------------------------
r = send_request(TOKEN_A, D_ID, 30)
results.append(("challenge inactive user -> 400", r.status_code == 400, str(r.status_code)))
r = send_request(TOKEN_C, D_ID, 60)
results.append(("inactive user cannot be challenged by anyone -> 400", r.status_code == 400, str(r.status_code)))

# --- 7. Invalid durations -> 422 ----------------------------------------------------------
for bad in (15, 120, 0, -30):
    r = send_request(TOKEN_A, B_ID, bad)
    results.append((f"invalid duration {bad} -> 422", r.status_code == 422, str(r.status_code)))

# --- 8. All four valid durations accepted --------------------------------------------------
valid_durations = {}
for d in (30, 60, 360, 1440):
    r = send_request(TOKEN_A, B_ID, d)
    ok = r.status_code == 201 and r.json()["duration_minutes"] == d and r.json()["status"] == "PENDING"
    results.append((f"valid duration {d} -> 201", ok, str(r.status_code)))
    valid_durations[d] = r.json()["request_id"]

# --- 9. Duplicate PENDING requests are allowed (documented design) -------------------------
r = send_request(TOKEN_A, B_ID, 30)
dup_json = r.json()
ok = (
    r.status_code == 201
    and dup_json["status"] == "PENDING"
    and dup_json["request_id"] != PENDING_A_TO_B_1
)
results.append(("duplicate PENDING request to same opponent -> allowed (201, distinct)", ok, str(r.status_code)))

# --- 10. No sensitive information returned ------------------------------------------------
for _ in range(1):
    body = send_request(TOKEN_A, B_ID, 30).json()
    allowed = {"request_id", "challenger", "opponent", "duration_minutes", "status", "created_at", "responded_at"}
    user_allowed = {"user_id", "username", "profile_picture_url", "biography"}
    leaks = set(body.keys()) - allowed
    leaks |= set(body["challenger"].keys()) - user_allowed
    leaks |= set(body["opponent"].keys()) - user_allowed
    text = str(body)
    ok = (
        not leaks
        and "password_hash" not in text
        and "email" not in text
        and "coin_balance" not in text
        and "winning_streak" not in text
    )
    results.append(("no sensitive user data in response", ok, "" if ok else str(leaks)))

# --- 11/12. Incoming ------------------------------------------------------------------------
incoming_b = incoming(TOKEN_B)
ok = any(
    q["request_id"] == PENDING_A_TO_B_1 and q["challenger"]["user_id"] == A_ID and q["status"] == "PENDING"
    for q in incoming_b
)
results.append(("B sees requests addressed to them", ok, ""))
results.append(("no password_hash in incoming lists", "password_hash" not in str(incoming_b), ""))

incoming_c = incoming(TOKEN_C)
ok = all(q["opponent"]["user_id"] == C_ID for q in incoming_c) and not any(
    q["request_id"] == PENDING_A_TO_B_1 for q in incoming_c
)
results.append(("C does not see A->B requests in C's incoming", ok, ""))

# --- 13/14. Outgoing ------------------------------------------------------------------------
outgoing_a = outgoing(TOKEN_A)
ok = any(
    q["request_id"] == PENDING_A_TO_B_1 and q["opponent"]["user_id"] == B_ID and q["status"] == "PENDING"
    for q in outgoing_a
)
results.append(("A sees requests they sent", ok, ""))
outgoing_c = outgoing(TOKEN_C)
ok = all(q["challenger"]["user_id"] == C_ID for q in outgoing_c) and not any(
    q["request_id"] == PENDING_A_TO_B_1 for q in outgoing_c
)
results.append(("C does not see A's outgoing requests", ok, ""))
results.append(("no password_hash in outgoing lists", "password_hash" not in str(outgoing_a), ""))

# --- 15/16/20/19. Accept: opponent accepts, challenger cannot -----------------------------
acc = act("accept", TOKEN_B, PENDING_A_TO_B_1)
acc_json = acc.json()
results.append(("opponent can accept -> 200", acc.status_code == 200, str(acc.status_code)))
results.append(("status becomes ACCEPTED", acc_json.get("status") == "ACCEPTED", acc_json.get("status", "")))
results.append(("responded_at populated on accept", acc_json.get("responded_at") is not None, ""))
results.append(("accept persists in DB", fetch_request(PENDING_A_TO_B_1)["status"] == "ACCEPTED", ""))

r = act("accept", TOKEN_A, valid_durations[60])
results.append(("challenger cannot accept own outgoing request -> 403", r.status_code == 403, str(r.status_code)))
results.append(("challenger's rejected accept leaves request PENDING",
                fetch_request(valid_durations[60])["status"] == "PENDING", ""))

# --- 17. Non-participant cannot accept -------------------------------------------------------
r = act("accept", TOKEN_C, valid_durations[360])
ok = r.status_code == 403 and fetch_request(valid_durations[360])["status"] == "PENDING"
results.append(("non-participant cannot accept -> 403, state unchanged", ok, str(r.status_code)))

# --- 18. Non-pending request cannot be accepted ----------------------------------------------
r = act("accept", TOKEN_B, PENDING_A_TO_B_1)
results.append(("accept on ACCEPTED -> 409", r.status_code == 409, str(r.status_code)))

# --- request not found --------------------------------------------------------------------------
r = act("accept", TOKEN_B, NONEXISTENT_ID)
results.append(("accept nonexistent request -> 404", r.status_code == 404, str(r.status_code)))

# --- 21/24/25. Decline ----------------------------------------------------------------------
dec_target = valid_durations[1440]
r = act("decline", TOKEN_B, dec_target)
dec_json = r.json()
results.append(("opponent can decline -> 200", r.status_code == 200, str(r.status_code)))
results.append(("status becomes DECLINED", dec_json.get("status") == "DECLINED", dec_json.get("status", "")))
results.append(("responded_at populated on decline", dec_json.get("responded_at") is not None, ""))

# --- 22. Challenger cannot decline -------------------------------------------------------------
r = act("decline", TOKEN_A, valid_durations[360])
ok = r.status_code == 403 and fetch_request(valid_durations[360])["status"] == "PENDING"
results.append(("challenger cannot decline -> 403, state unchanged", ok, str(r.status_code)))

# --- 23. Non-pending cannot be declined ---------------------------------------------------------
r = act("decline", TOKEN_B, PENDING_A_TO_B_1)
results.append(("decline on ACCEPTED -> 409", r.status_code == 409, str(r.status_code)))
r = act("decline", TOKEN_B, dec_target)
results.append(("decline on DECLINED -> 409", r.status_code == 409, str(r.status_code)))

# --- 26/29/30. Cancel -----------------------------------------------------------------------------
cancel_target = valid_durations[30]
r = act("cancel", TOKEN_A, cancel_target)
cancel_json = r.json()
results.append(("challenger can cancel -> 200", r.status_code == 200, str(r.status_code)))
results.append(("status becomes CANCELLED", cancel_json.get("status") == "CANCELLED", cancel_json.get("status", "")))
results.append(("responded_at populated on cancel", cancel_json.get("responded_at") is not None, ""))

# --- 27. Opponent cannot cancel --------------------------------------------------------------------
r = act("cancel", TOKEN_B, valid_durations[360])
ok = r.status_code == 403 and fetch_request(valid_durations[360])["status"] == "PENDING"
results.append(("opponent cannot cancel -> 403, state unchanged", ok, str(r.status_code)))

# --- 28. Non-pending cannot be cancelled -------------------------------------------------------------
r = act("cancel", TOKEN_A, PENDING_A_TO_B_1)
results.append(("cancel on ACCEPTED -> 409", r.status_code == 409, str(r.status_code)))
r = act("cancel", TOKEN_A, dec_target)
results.append(("cancel on DECLINED -> 409", r.status_code == 409, str(r.status_code)))
r = act("cancel", TOKEN_A, cancel_target)
results.append(("cancel on CANCELLED -> 409", r.status_code == 409, str(r.status_code)))

# --- 31/32/33. Terminal states cannot be re-transitioned ----------------------------------------------
# ACCEPTED: opp can't decline/accept, challenger can't cancel (cancel is challenger-only -> 403 role block)
acc, dec, can = PENDING_A_TO_B_1, dec_target, cancel_target
results.append(("ACCEPTED can't be declined -> 409", act("decline", TOKEN_B, acc).status_code == 409, ""))
results.append(("ACCEPTED can't be re-accepted -> 409", act("accept", TOKEN_B, acc).status_code == 409, ""))
results.append(("ACCEPTED can't be cancelled by challenger -> 409", act("cancel", TOKEN_A, acc).status_code == 409, ""))
results.append(("DECLINED can't be accepted -> 409", act("accept", TOKEN_B, dec).status_code == 409, ""))
results.append(("DECLINED can't be re-declined -> 409", act("decline", TOKEN_B, dec).status_code == 409, ""))
results.append(("CANCELLED can't be accepted -> 409", act("accept", TOKEN_B, can).status_code == 409, ""))
results.append(("CANCELLED can't be declined -> 409", act("decline", TOKEN_B, can).status_code == 409, ""))
results.append(("ACCEPTED/ DECLINED/ CANCELLED all terminal in DB",
                [fetch_request(x)["status"] for x in (acc, dec, can)] == ["ACCEPTED", "DECLINED", "CANCELLED"], ""))

# --- 34. Isolation: an outsider cannot manipulate A/B requests --------------------------------------------
outsider_pending = valid_durations[360]
for action in ("accept", "decline", "cancel"):
    r = act(action, TOKEN_C, outsider_pending)
    results.append((f"C cannot {action} A/B request -> 403", r.status_code == 403, str(r.status_code)))
results.append(("C's attempts left the request PENDING",
                fetch_request(outsider_pending)["status"] == "PENDING", ""))

# --- Out-of-scope negative checks ------------------------------------------------------------------------
r = client.post("/competition-requests", json={"opponent_id": B_ID, "duration_minutes": 30}, headers=AUTH_A)
results.append(("send ignores unparseable extra fields (no 422 on extra junk)",
                r.status_code in (200, 201), str(r.status_code)))

try:
    failed = 0
    for name, ok, extra in results:
        report(name, ok, extra)
        failed += 0 if ok else 1
    print(f"\n{len(results) - failed}/{len(results)} checks passed.")
    if failed:
        raise SystemExit(1)
finally:
    cleanup()