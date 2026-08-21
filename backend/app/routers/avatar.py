"""
Avatar — the authenticated user's current avatar state (Phase 5).

GET /avatar returns the caller's avatar together with its COMPLETE
currently-equipped clothing in ONE response, so the mobile client can
render the avatar without one request per slot. Customization itself is
NOT duplicated here: equipping/unequipping stays with the wardrobe
endpoints (POST|DELETE /wardrobe/{wardrobe_id}/equip), which are the
single writers of avatar_equipment — this endpoint only exposes the
resulting state:

    wardrobe equip/unequip -> avatar_equipment -> GET /avatar

Design notes
------------
- Data isolation: the avatar comes ONLY from the JWT
  (get_current_user() -> current_user.user_id) filtered directly in the
  SQL WHERE clause. The endpoint declares no user_id/avatar_id input, so
  there is no client value that could redirect the query at another
  user's avatar; a smuggled ?user_id=… query param is ignored by FastAPI.
- Missing avatar: avatars.user_id is UNIQUE (0-or-1 per user) and
  registration creates the avatar in the same transaction as the user,
  so a missing avatar is not a state this flow can produce anymore; if
  it is ever seen anyway (e.g. a legacy row predating the backfill), it
  is reported explicitly as 404 "Avatar not found" — the same
  convention the equip/unequip endpoints use — rather than silently
  created inside a read endpoint.
- No N+1: the avatar, its equipment rows, their items and the items'
  categories load in ONE query via
  joinedload(Avatar.equipment) -> joinedload(AvatarEquipment.item)
  -> joinedload(ClothingItem.category). An avatar has at most six
  equipment rows ((avatar_id, slot) PK), so the row-deduplicated join is
  cheap and no per-slot queries exist.
- Slot authority: slots come from avatar_equipment.slot, which the equip
  flow derived from clothing_items.category_id -> clothing_categories.slot
  at equip time; the nested item payload carries the same slot again via
  its category ref. Nothing recomputes or overrides it here.
- Availability independence: NO filter on clothing_items.availability_status.
  If an admin marks an equipped item UNAVAILABLE/UPCOMING, the avatar
  still reports it — availability governs buying, not wearing (mirrors
  the wardrobe rule).
- Empty slots: every one of the six AvatarSlot values is always present
  in the response; an empty slot is an explicit null (see
  AvatarEquipmentMap). A defensive note: avatar_equipment.item_id is
  nullable at the schema level, so an equipment row without an item
  (which the equip flow never writes) is treated as an empty slot too.
- Read-only: this endpoint performs no writes and never touches
  coin_balance or the ledger.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import (
    Avatar,
    AvatarEquipment,
    AvatarSlot,
    ClothingItem,
    User,
)
from app.schemas.avatar import AvatarEquipmentMap, AvatarRead, AvatarSlotEquipment
from app.schemas.clothing import ClothingCategoryRef, ClothingItemRead

router = APIRouter(prefix="/avatar", tags=["avatar"])


@router.get("", response_model=AvatarRead)
def get_my_avatar(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AvatarRead:
    # The owner filter uses ONLY the JWT-derived id; there is no other
    # input this query accepts, so cross-user reads are impossible.
    avatar = db.execute(
        select(Avatar)
        .where(Avatar.user_id == current_user.user_id)
        .options(
            joinedload(Avatar.equipment)
            .joinedload(AvatarEquipment.item)
            .joinedload(ClothingItem.category)
        )
    ).unique().scalar_one_or_none()
    if avatar is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Avatar not found",
        )

    # Fold the equipment rows into the slot-keyed map. Empty slots keep
    # their schema default (None); an item-less equipment row (possible
    # at the schema level, never written by the equip flow) counts as
    # empty as well.
    occupied: dict[str, AvatarSlotEquipment] = {}
    for entry in avatar.equipment:
        if entry.item is None:
            continue
        occupied[entry.slot.value] = AvatarSlotEquipment(
            equipped_at=entry.equipped_at,
            item=_to_item_read(entry.item),
        )

    return AvatarRead(
        avatar_id=avatar.avatar_id,
        equipment=AvatarEquipmentMap(**occupied),
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
