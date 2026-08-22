"""
Pydantic schema for the `competitions` table (Phase 6, Part 2).

CompetitionRead is the safe response shape for GET /competitions/{id}. It
embeds UserPublic for both participants and the lookup table's status_name
("active" / "completed") — never password_hash, email, coin_balance, or any
other account field. winner_id is exposed only when a winner exists (null
until competition completion is implemented).
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.user import UserPublic


class CompetitionRead(BaseModel):
    competition_id: UUID
    request_id: UUID
    challenger: UserPublic
    opponent: UserPublic
    status: str
    prize_pool: int
    total_votes: int
    winner_id: UUID | None = None
    duration_minutes: int
    start_time: datetime
    end_time: datetime
    created_at: datetime