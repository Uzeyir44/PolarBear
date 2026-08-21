"""
Wardrobe — the authenticated user's purchased clothing (Phase 4, part 1).

GET /wardrobe lists the caller's user_wardrobe ownership records, newest
purchase first, paginated with limit/offset. Read-only: equipping/
unequipping lives in avatar_equipment and is a later phase.

Design notes
------------
- Data isolation: the wardrobe is strictly private. The owner comes ONLY
  from the JWT (get_current_user() -> current_user.user_id) and is used
  directly in the SQL WHERE clause — the endpoint declares no user_id
  path/query/body parameter, so there is no client input that could
  redirect the query at another user's rows.
- Availability rule: NO filter on clothing_items.availability_status.
  Browsing filters to AVAILABLE because that is the purchasable shelf;
  the wardrobe is an ownership history. If an admin later marks an owned
  item UNAVAILABLE/UPCOMING, existing owners must still see it —
  availability governs buying, not owning (the admin delete endpoint's
  "mark it UNAVAILABLE instead of deleting" rule depends on this).
- No N+1: each page loads its items with
  joinedload(UserWardrobe.item).joinedload(ClothingItem.category), so a
  page of N entries is ONE query; the count is a second cheap query.
- Deterministic ordering: purchased_at DESC with wardrobe_id DESC as the
  tiebreaker (server-side now() can give two purchases in one commit the
  same timestamp), the same pattern the transactions/catalog lists use,
  so repeated requests over the same data paginate stably.
- Empty wardrobe: a user who has never purchased simply gets 200 with
  items=[] and total=0 — an empty closet is a valid state, not a 404.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import ClothingItem, User, UserWardrobe
from app.schemas.clothing import ClothingCategoryRef, ClothingItemRead
from app.schemas.wardrobe import WardrobeEntryRead, WardrobeList

router = APIRouter(prefix="/wardrobe", tags=["wardrobe"])


@router.get("", response_model=WardrobeList)
def list_my_wardrobe(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="How many wardrobe entries to return (1-100)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="How many entries to skip before returning results",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WardrobeList:
    # The owner filter uses ONLY the JWT-derived id; there is no other
    # input this query accepts, so cross-user reads are impossible.
    owner_id = current_user.user_id

    total = db.execute(
        select(func.count(UserWardrobe.wardrobe_id)).where(
            UserWardrobe.user_id == owner_id
        )
    ).scalar_one()

    rows = db.execute(
        select(UserWardrobe)
        .where(UserWardrobe.user_id == owner_id)
        .options(
            joinedload(UserWardrobe.item).joinedload(ClothingItem.category)
        )
        .order_by(UserWardrobe.purchased_at.desc(), UserWardrobe.wardrobe_id.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    return WardrobeList(
        items=[_to_entry_read(entry) for entry in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _to_entry_read(entry: UserWardrobe) -> WardrobeEntryRead:
    item = entry.item
    return WardrobeEntryRead(
        wardrobe_id=entry.wardrobe_id,
        purchased_at=entry.purchased_at,
        item=ClothingItemRead(
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
        ),
    )

