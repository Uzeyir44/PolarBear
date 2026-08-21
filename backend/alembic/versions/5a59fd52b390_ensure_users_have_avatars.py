"""ensure users have avatars

Revision ID: 5a59fd52b390
Revises: e833bac26dfc
Create Date: 2026-08-21 13:37:41.878382

DATA-ONLY backfill: registration now creates an avatar in the same
transaction as the user, but users registered before that fix have no
avatars row — which made GET /avatar and wardrobe equip/unequip fail
with 404 "Avatar not found" for them. This revision inserts one avatar
per user that does not already have one.

The statement is idempotent by construction: the NOT EXISTS guard means
re-running it (or running it against a database where some users already
have avatars) can never create a second row for the same user, and the
existing avatars.user_id UNIQUE constraint stays untouched and enforced.
avatar_id and created_at are left to their server defaults
(gen_random_uuid() / now()), matching what the ORM produces.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a59fd52b390'
down_revision: Union[str, Sequence[str], None] = 'e833bac26dfc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill: one avatar for every user without one."""
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO avatars (user_id, created_at) "
            "SELECT u.user_id, now() "
            "FROM users u "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM avatars a WHERE a.user_id = u.user_id"
            ")"
        )
    )


def downgrade() -> None:
    """Remove the avatars this backfill created.

    A backfilled avatar is always EMPTY (no avatar_equipment rows can
    exist for an avatar that was just created), so deleting only
    equipment-less avatars is a precise reversal for the state this
    revision was authored in (the avatars table was empty). Caveat: if
    downgrade runs after new registrations have created further empty
    avatars, those are removed too — downgrading past this point is only
    meaningful in early development.
    """
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM avatars a "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM avatar_equipment e WHERE e.avatar_id = a.avatar_id"
            ")"
        )
    )
