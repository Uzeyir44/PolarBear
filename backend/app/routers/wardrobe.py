"""
Wardrobe — the authenticated user's purchased clothing (Phase 4).

GET /wardrobe (part 1) lists the caller's user_wardrobe ownership
records, newest purchase first, paginated with limit/offset.

POST /wardrobe/{wardrobe_id}/equip and DELETE
/wardrobe/{wardrobe_id}/equip (part 2) equip/unequip an owned item into
the slot its category dictates. Avatar customization/retrieval is a
later phase.

Design notes
------------
- Data isolation: the wardrobe is strictly private. The owner comes ONLY
  from the JWT (get_current_user() -> current_user.user_id) and is used
  directly in the SQL WHERE clause — the endpoints declare no user_id,
  avatar_id, item_id or slot input, so there is no client value that
  could redirect any query at another user's rows. Equip/unequip look up
  the ownership row by (wardrobe_id, user_id) in ONE query; another
  user's wardrobe_id is indistinguishable from a nonexistent one -> 404.
- Availability rule: NO filter on clothing_items.availability_status.
  Browsing filters to AVAILABLE because that is the purchasable shelf;
  the wardrobe is an ownership history. If an admin later marks an owned
  item UNAVAILABLE/UPCOMING, existing owners must still see and wear it —
  availability governs buying, not owning.
- No N+1: each page loads its items with
  joinedload(UserWardrobe.item).joinedload(ClothingItem.category), so a
  page of N entries is ONE query; the count is a second cheap query.
- Deterministic ordering: purchased_at DESC with wardrobe_id DESC as the
  tiebreaker (server-side now() can give two purchases in one commit the
  same timestamp), the same pattern the transactions/catalog lists use,
  so repeated requests over the same data paginate stably.
- Empty wardrobe: a user who has never purchased simply gets 200 with
  items=[] and total=0 — an empty closet is a valid state, not a 404.

Equip/unequip specifics:

- Slot authority: the slot comes ONLY from
  clothing_items.category_id -> clothing_categories.slot. The client
  cannot name a slot; sunglasses always land in ACCESSORY because their
  category says so.
- Avatar resolution: avatars.user_id is UNIQUE (0-or-1 per user) and
  registration creates the avatar in the same transaction as the user,
  so a missing avatar is not a state this flow can produce anymore; if
  it is ever seen anyway (e.g. a legacy row predating the backfill), it
  is reported explicitly as 404 "Avatar not found" rather than silently
  created here — avatar creation belongs to registration, not to a
  clothing endpoint.
- Replacement semantics: equipping into an occupied slot REPLACES the
  old item. The displaced item stays owned in user_wardrobe; nothing is
  charged and no ledger row is written — purchasing and equipping are
  separate operations.
- Concurrency: equip is ONE PostgreSQL upsert — INSERT ... ON CONFLICT
  (avatar_id, slot) DO UPDATE SET item_id = excluded.item_id,
  equipped_at = now() — so the avatar_equipment primary key remains the
  structural guarantee of one item per slot. Two concurrent equips for
  the same slot serialize on that index entry and the final state is
  exactly one row (last writer wins); no read-modify-write race exists
  because each request fully specifies the new state. Unequip is one
  DELETE guarded by all three columns (avatar_id, slot, item_id), so it
  can only remove THIS item from THIS slot.
- Atomicity: each endpoint performs its checks as plain reads and its
  change as a single statement, then commits once; get_db() rolls the
  session back on any failure, so partial equipment state is impossible.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import Avatar, AvatarEquipment, AvatarSlot, ClothingItem, User, UserWardrobe
from app.schemas.clothing import ClothingCategoryRef, ClothingItemRead
from app.schemas.wardrobe import (
    EquipResult,
    EquipmentRead,
    UnequipResult,
    WardrobeEntryRead,
    WardrobeList,
)

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


@router.post("/{wardrobe_id}/equip", response_model=EquipResult)
def equip_wardrobe_item(
    wardrobe_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EquipResult:
    # The URL's {wardrobe_id} is parsed as a uuid.UUID (malformed -> 422)
    # and is the ONLY client input. Ownership is enforced by filtering on
    # it together with the JWT-derived user_id in ONE query: another
    # user's wardrobe_id and a nonexistent one are both just 404.
    entry = db.execute(
        select(UserWardrobe)
        .options(joinedload(UserWardrobe.item).joinedload(ClothingItem.category))
        .where(
            UserWardrobe.wardrobe_id == wardrobe_id,
            UserWardrobe.user_id == current_user.user_id,
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wardrobe item not found",
        )

    # avatars.user_id is UNIQUE (0-or-1) and registration creates the
    # avatar in the same transaction as the user, so this lookup should
    # always resolve; if it ever doesn't (a legacy row predating the
    # backfill), report it explicitly instead of silently creating an
    # avatar inside a clothing endpoint.
    avatar_id = db.execute(
        select(Avatar.avatar_id).where(Avatar.user_id == current_user.user_id)
    ).scalar_one_or_none()
    if avatar_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Avatar not found",
        )

    # The category is authoritative for the slot; no client slot input exists.
    slot: AvatarSlot = entry.item.category.slot

    # Snapshot the response payload BEFORE the commit expires the ORM
    # instances (expire_on_commit) — no lazy reloads afterwards.
    item_read = _to_item_read(entry.item)

    # One atomic upsert: inserts when the slot is empty, replaces the item
    # (and refreshes equipped_at) when it is occupied. The (avatar_id, slot)
    # primary key stays the structural one-item-per-slot guarantee; two
    # concurrent equips serialize on that index entry and exactly one row
    # survives — last writer wins, which is the intended semantics.
    # RETURNING hands back the server-written equipped_at in the same
    # statement, the way the purchase flow returns its new balance.
    equipped_at = db.execute(
        pg_insert(AvatarEquipment)
        .values(avatar_id=avatar_id, slot=slot, item_id=entry.item_id)
        .on_conflict_do_update(
            index_elements=[AvatarEquipment.avatar_id, AvatarEquipment.slot],
            set_={
                AvatarEquipment.item_id: entry.item_id,
                AvatarEquipment.equipped_at: func.now(),
            },
        )
        .returning(AvatarEquipment.equipped_at)
    ).scalar_one()
    db.commit()

    return EquipResult(
        message="Item equipped successfully",
        equipment=EquipmentRead(
            avatar_id=avatar_id,
            slot=slot,
            equipped_at=equipped_at,
            item=item_read,
        ),
    )


@router.delete("/{wardrobe_id}/equip", response_model=UnequipResult)
def unequip_wardrobe_item(
    wardrobe_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UnequipResult:
    # Same ownership rule as equip: (wardrobe_id AND jwt user_id) in one
    # query; someone else's wardrobe_id is 404, never an unequip.
    entry = db.execute(
        select(UserWardrobe)
        .options(joinedload(UserWardrobe.item).joinedload(ClothingItem.category))
        .where(
            UserWardrobe.wardrobe_id == wardrobe_id,
            UserWardrobe.user_id == current_user.user_id,
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wardrobe item not found",
        )

    avatar_id = db.execute(
        select(Avatar.avatar_id).where(Avatar.user_id == current_user.user_id)
    ).scalar_one_or_none()
    if avatar_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Avatar not found",
        )

    slot: AvatarSlot = entry.item.category.slot

    # Delete ONLY the row where THIS item occupies THIS slot of THIS
    # avatar. If a different item is equipped there (or the slot is
    # empty), nothing matches -> 409, and Shirt A can never be removed
    # by asking to unequip Shirt B.
    deleted = db.execute(
        delete(AvatarEquipment).where(
            AvatarEquipment.avatar_id == avatar_id,
            AvatarEquipment.slot == slot,
            AvatarEquipment.item_id == entry.item_id,
        )
    ).rowcount
    if deleted == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This item is not currently equipped",
        )
    db.commit()

    return UnequipResult(
        message="Item unequipped successfully",
        avatar_id=avatar_id,
        slot=slot,
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


def _to_entry_read(entry: UserWardrobe) -> WardrobeEntryRead:
    return WardrobeEntryRead(
        wardrobe_id=entry.wardrobe_id,
        purchased_at=entry.purchased_at,
        item=_to_item_read(entry.item),
    )

