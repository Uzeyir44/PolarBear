"""
Pydantic schemas for the wardrobe listing (GET /wardrobe).

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
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

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
