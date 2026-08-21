"""
Pydantic schemas for the wardrobe (Phase 4).

Listing (GET /wardrobe):
WardrobeEntryRead is ONE ownership record: the user_wardrobe row's own
fields (wardrobe_id, purchased_at) plus the owned item rendered in the
exact same shape the shop browse/purchase endpoints use
(ClothingItemRead, which nests its ClothingCategoryRef with the slot).
Reusing those schemas keeps one canonical item payload across the app —
the mobile client renders a wardrobe card with the same code it uses for
a catalog card. The item is included even when an admin later flips its
availability_status to UNAVAILABLE/UPCOMING: ownership history does not
disappear when a catalog entry stops being purchasable.

WardrobeList is the paginated envelope, matching the project's
items/total/limit/offset convention used by GET /clothing.

Equipment (POST / DELETE /wardrobe/{wardrobe_id}/equip):
EquipmentRead is the current state of ONE avatar_equipment slot — which
avatar, which slot, what is equipped there (again the shared catalog
item shape), and when it was equipped. EquipResult wraps it after a
successful equip; UnequipResult reports the slot that was cleared (no
item — it is no longer equipped). None of these expose anything beyond
the avatar/slot/item context the client needs to update the avatar UI.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models import AvatarSlot
from app.schemas.clothing import ClothingItemRead


class WardrobeEntryRead(BaseModel):
    """One user_wardrobe ownership record with its clothing item."""

    wardrobe_id: uuid.UUID
    purchased_at: datetime
    item: ClothingItemRead


class WardrobeList(BaseModel):
    """Paginated response of GET /wardrobe.

    total is the count of ALL of the user's wardrobe rows (no
    availability filter — owned items stay visible regardless of catalog
    status), so a client can page through a stable result set.
    items/limit/offset describe the page.
    """

    items: list[WardrobeEntryRead]
    total: int
    limit: int
    offset: int


class EquipmentRead(BaseModel):
    """Current equipment state of one avatar slot."""

    avatar_id: uuid.UUID
    slot: AvatarSlot
    equipped_at: datetime
    item: ClothingItemRead


class EquipResult(BaseModel):
    """Response of POST /wardrobe/{wardrobe_id}/equip."""

    message: str
    equipment: EquipmentRead


class UnequipResult(BaseModel):
    """Response of DELETE /wardrobe/{wardrobe_id}/equip.

    The slot was cleared, so only its identity comes back — carrying the
    item would suggest it is still equipped.
    """

    message: str
    avatar_id: uuid.UUID
    slot: AvatarSlot
