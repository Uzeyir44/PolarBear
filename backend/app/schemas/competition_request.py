"""
Pydantic schemas for competition requests (Phase 6, Part 1).

CompetitionRequestCreate defines the ONLY fields the client may send. The
backend owns challenger_id, status, created_at, and responded_at, so they
cannot appear here; Pydantic ignores any extra fields sent by the client.

CompetitionRequestRead is the safe response shape. It embeds UserPublic for
both participants — never password_hash, email, coin_balance, or any other
account field (matching the /users/search convention).
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.user import UserPublic

# Single source of truth for the four allowed durations, mirroring the DB
# CheckConstraint ck_competition_requests_duration_allowed_values. Declared
# here as a Literal so invalid values (e.g. 120, 0, negative) are rejected
# by FastAPI with a 422 before the request ever reaches the handler.
AllowedCompetitionDurations = Literal[30, 60, 360, 1440]

# DB native enum stores the member NAME ("PENDING", not "pending"); the same
# convention as coin_transaction_types.direction, which the API exposes as
# "CREDIT"/"DEBIT".
CompetitionRequestStatusLiteral = Literal[
    "PENDING",
    "ACCEPTED",
    "DECLINED",
    "CANCELLED",
]


class CompetitionRequestCreate(BaseModel):
    opponent_id: UUID
    duration_minutes: AllowedCompetitionDurations


class CompetitionRequestRead(BaseModel):
    request_id: UUID
    challenger: UserPublic
    opponent: UserPublic
    duration_minutes: int
    status: CompetitionRequestStatusLiteral
    created_at: datetime
    responded_at: datetime | None = None