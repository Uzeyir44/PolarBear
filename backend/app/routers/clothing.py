"""
Clothing shop — browsing (Phase 3, step 1).

GET /clothing lists the purchasable catalog for an authenticated user,
paginated with limit/offset, with an optional category filter.

Design notes
------------
- Availability rule: browsing exposes ONLY items whose
  availability_status is AVAILABLE. UNAVAILABLE items are sold out /
  withdrawn catalog entries (the design doc says to set
  availability_status='unavailable' instead of deleting them), and
  UPCOMING items are not purchasable yet — neither belongs in the shop
  shelf a user can buy from. This is an explicit product decision: no
  future change makes it "wrong" to keep them hidden; adding an admin
  "preview upcoming" surface later would be additive, not a correction.
- Category filter: category_id is validated against clothing_categories
  (a real SMALLINT id, never a hard-coded name). A well-formed id that
  does not exist returns 404, matching the codebase's "specific lookup
  failed" convention (db.get(...) -> 404) rather than silently returning
  an empty list.
- No N+1: the item rows are loaded with joinedload(category), so the
  catalog page and its category context come back in one query.
- Deterministic ordering: created_at DESC with item_id DESC as the
  tiebreaker, the same pattern the transactions/admin lists use, so
  repeated requests over the same data paginate stably.
- Only AVAILABLE items are counted in `total`, so the client can page
  reliably against the same rule the page uses.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import ClothingAvailability, ClothingCategory, ClothingItem, User
from app.schemas.clothing import ClothingCategoryRef, ClothingItemList, ClothingItemRead

router = APIRouter(prefix="/clothing", tags=["clothing"])


@router.get("", response_model=ClothingItemList)
def list_clothing_items(
    category_id: int | None = Query(
        default=None,
        ge=1,
        le=32767,
        description="Filter by a clothing category id (a SMALLINT, e.g. "
        "1 = Hairstyles). A nonexistent in-range id returns 404.",
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="How many clothing items to return (1-100)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="How many items to skip before returning results",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClothingItemList:
    conditions = [ClothingItem.availability_status == ClothingAvailability.AVAILABLE]

    if category_id is not None:
        # Fail fast on a category that doesn't exist instead of silently
        # returning an empty page for a bogus filter.
        if db.get(ClothingCategory, category_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Category not found",
            )
        conditions.append(ClothingItem.category_id == category_id)

    total = db.execute(
        select(func.count(ClothingItem.item_id)).where(*conditions)
    ).scalar_one()

    rows = db.execute(
        select(ClothingItem)
        .options(joinedload(ClothingItem.category))
        .where(*conditions)
        .order_by(ClothingItem.created_at.desc(), ClothingItem.item_id.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    return ClothingItemList(
        items=[_to_item_read(item) for item in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _to_item_read(item: ClothingItem) -> ClothingItemRead:
    return ClothingItemRead(
        item_id=item.item_id,
        name=item.name,
        description=item.description,
        category=ClothingCategoryRef(
            category_id=item.category.category_id,
            category_name=item.category.category_name,
            slot=item.category.slot,
        ),
        price=item.price,
        image_url=item.image_url,
        availability_status=item.availability_status,
        collection_id=item.collection_id,
    )
