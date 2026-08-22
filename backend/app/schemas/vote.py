"""
Pydantic schemas for votes — Phase 6, Part 4B-1 (basic vote casting).

VoteCreate is the ONLY field the client supplies (who they vote for);
competition_id, voter_id, and created_at are owned by the backend. VoteRead
is the safe confirmation response — ids + timestamp + the fresh total_votes;
no password/account/coin fields.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class VoteCreate(BaseModel):
    voted_for_user_id: UUID


class VoteRead(BaseModel):
    vote_id: UUID
    competition_id: UUID
    voter_id: UUID
    voted_for_user_id: UUID
    created_at: datetime
    total_votes: int