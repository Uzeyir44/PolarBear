"""
Pydantic schemas for retrieving the current user's avatar (Phase 5).

AvatarRead is the response of GET /avatar: the caller's avatar_id plus
the COMPLETE current equipment state, so ONE request gives the mobile
client everything needed to render the avatar (no per-slot requests).

The equipment map has one explicit field per AvatarSlot. The slot set is
closed and stable (see app/models/enums.py), so spelling the six slots
out documents the exact response shape in OpenAPI and guarantees every
slot key is always present — an empty slot is an explicit null, never a
missing key.

Each occupied slot is an AvatarSlotEquipment: when the item was equipped
plus the item itself rendered in the canonical catalog shape
(ClothingItemRead, which nests its ClothingCategoryRef with the slot) —
the same payload the shop, wardrobe and equip endpoints use, so the
client renders an equipped piece with the same code it renders a catalog
card. No availability filter is applied here on purpose: an equipped
item stays reported even if an admin later marks it UNAVAILABLE/UPCOMING
(availability governs buying, not wearing).

Nothing user-identifying beyond the avatar/slot/item context is exposed:
no email, password hash, auth-provider, balance or admin fields.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.clothing import ClothingItemRead


class AvatarSlotEquipment(BaseModel):
    """Current state of ONE avatar slot that holds an item."""

    equipped_at: datetime
    item: ClothingItemRead


class AvatarEquipmentMap(BaseModel):
    """The complete equipment state of one avatar, keyed by slot.

    Every one of the six AvatarSlot values is always present; a slot
    with nothing equipped is null.
    """

    hair: AvatarSlotEquipment | None = None
    hat: AvatarSlotEquipment | None = None
    top: AvatarSlotEquipment | None = None
    bottom: AvatarSlotEquipment | None = None
    shoes: AvatarSlotEquipment | None = None
    accessory: AvatarSlotEquipment | None = None


class AvatarRead(BaseModel):
    """Response of GET /avatar — the caller's avatar and what it wears."""

    avatar_id: uuid.UUID
    equipment: AvatarEquipmentMap
