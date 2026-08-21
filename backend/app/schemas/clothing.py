"""
Pydantic schemas for browsing the clothing shop (GET /clothing).

ClothingItemRead is the output schema for ONE clothing item as seen by a
authenticated client. It deliberately exposes only the public catalog
fields: nothing internal (category_id as a raw id is folded into the
nested `category` object), no created_at housekeeping, and the
availability_status comes from the ClothingAvailability enum so it
serializes to the friendly lowercase value ("available"). collection_id
is surfaced because it is already a documented, non-sensitive catalog
field (the design doc reserves it for future collections).

ClothingCategoryRef is the category context nested inside each item; it
carries the slot so clients know which avatar slot the item fills without
a second lookup.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.models import AvatarSlot, ClothingAvailability


class ClothingCategoryRef(BaseModel):
    """Category context for one clothing item.

    `slot` is non-sensitive and useful to the client — it says which
    avatar slot items in this category equip into.
    """

    category_id: int
    category_name: str
    slot: AvatarSlot


class ClothingItemRead(BaseModel):
    """One clothing item as exposed to a browsing client."""

    item_id: uuid.UUID
    name: str
    description: str | None = None
    category: ClothingCategoryRef
    price: int
    image_url: str
    availability_status: ClothingAvailability
    collection_id: uuid.UUID | None = None


class ClothingItemList(BaseModel):
    """Paginated response of GET /clothing.

    total is the count of items matching the current availability rule
    (AVAILABLE) and the chosen category filter, so a client can page
    through a stable result set. items/limit/offset describe the page.
    """

    items: list[ClothingItemRead]
    total: int
    limit: int
    offset: int


class ClothingPurchaseResult(BaseModel):
    """Response of POST /clothing/{item_id}/purchase.

    Everything a client needs to confirm a purchase in one payload:
    the bought item (same shape as the catalog view), the user_wardrobe
    ownership record id, how many coins the purchase cost (positive
    number — the debit sign lives in the ledger, not here), the
    remaining coin_balance after the debit, and the coin_transactions
    ledger row id that recorded it.
    """

    message: str
    wardrobe_id: uuid.UUID
    item: ClothingItemRead
    amount_spent: int
    remaining_balance: int
    transaction_id: uuid.UUID