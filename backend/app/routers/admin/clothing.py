"""
Admin clothing management — manage the SAME clothing_items catalog the
user-facing shop browses and purchases from. No second table, no separate
model: every operation here reads/writes clothing_items and validates
categories against clothing_categories.

Covers:
  GET    /admin/clothing/categories      lookup rows for the admin form's
                                         category dropdown (id/name/slot)
  GET    /admin/clothing                 paginated list, newest first, with
                                         name search + category/availability
                                         filters (admins see ALL statuses,
                                         unlike public browse)
  GET    /admin/clothing/{item_id}       administrative detail + 404
  POST   /admin/clothing                 create (category must exist)
  PATCH  /admin/clothing/{item_id}       update catalog fields
  DELETE /admin/clothing/{item_id}       delete ONLY if unreferenced

Every endpoint inherits get_current_admin() from the /admin package router:
unauthenticated -> 401, authenticated non-admin -> 403.

Design notes
------------
- item_id and created_at are database-owned. The create/update schemas
  contain no fields for them, so a client can never set or change them.
- The slot is NEVER client-supplied: it lives on the clothing_categories
  row and is inherited through the category relationship. An unknown
  category_id is rejected with 404 ("Category not found"), matching the
  public browse endpoint's convention for a well-formed but nonexistent id.
- collection_id stays a plain nullable UUID column exactly as modeled —
  clothing_collections does not exist yet (documented future extension
  point), so there is nothing to validate it against beyond UUID format.
- Deletion is deliberately conservative. user_wardrobe.item_id and
  avatar_equipment.item_id are both ondelete=RESTRICT FKs, and ownership/
  equipment rows are historical records — a referenced item must NOT be
  deleted. The endpoint counts both references first and returns 409
  "Clothing item cannot be deleted because users own or wear it. Mark it
  UNAVAILABLE instead." only an unreferenced item is deleted. This matches
  the design doc's rule (set availability_status='unavailable' instead of
  deleting) and the products module's guarded-delete pattern; no soft-delete
  column is needed because availability_status already provides it.
- Deterministic ordering: created_at DESC with item_id DESC as tiebreaker —
  the same pattern as the public browse list and the other admin lists.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies import get_current_admin
from app.models import (
    AvatarEquipment,
    ClothingAvailability,
    ClothingCategory,
    ClothingItem,
    User,
    UserWardrobe,
)
from app.routers.users import escape_like
from app.schemas.clothing_admin import (
    ClothingAdminCategory,
    ClothingAdminCreate,
    ClothingAdminList,
    ClothingAdminRead,
    ClothingAdminUpdate,
)

router = APIRouter(tags=["admin"])

REFERENCED_ERROR = (
    "Clothing item cannot be deleted because users own or wear it. "
    "Mark it UNAVAILABLE instead."
)
CATEGORY_NOT_FOUND = "Category not found"


# NOTE: registered BEFORE /{item_id} so the static path always wins over
# the uuid path parameter.
@router.get("/categories", response_model=list[ClothingAdminCategory])
def list_clothing_categories(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> list[ClothingCategory]:
    """The clothing_categories lookup rows for the admin form dropdown."""
    return db.execute(
        select(ClothingCategory).order_by(ClothingCategory.category_id)
    ).scalars().all()


@router.get("", response_model=ClothingAdminList)
def list_clothing_items(
    q: str | None = Query(
        default=None,
        max_length=50,
        description="Search fragment for item name (partial, case-insensitive)",
    ),
    category_id: int | None = Query(
        default=None,
        ge=1,
        le=32767,
        description="Filter by a clothing category id. A nonexistent in-range "
        "id returns 404.",
    ),
    availability: ClothingAvailability | None = Query(
        default=None,
        description="Filter by availability status",
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
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> ClothingAdminList:
    conditions = []
    if q:
        # Same ILIKE escaping rules as the users/products search surfaces.
        pattern = f"%{escape_like(q.strip())}%"
        conditions.append(
            or_(
                ClothingItem.name.ilike(pattern, escape="\\"),
                ClothingItem.description.ilike(pattern, escape="\\"),
            )
        )
    if category_id is not None:
        # Fail fast on a category that doesn't exist instead of silently
        # returning an empty page for a bogus filter (public browse does
        # the same).
        if db.get(ClothingCategory, category_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=CATEGORY_NOT_FOUND,
            )
        conditions.append(ClothingItem.category_id == category_id)
    if availability is not None:
        conditions.append(ClothingItem.availability_status == availability)

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

    return ClothingAdminList(
        items=[_to_admin_read(item) for item in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{item_id}", response_model=ClothingAdminRead)
def get_clothing_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> ClothingAdminRead:
    item = _get_item(db, item_id)
    return _to_admin_read(item)


@router.post("", response_model=ClothingAdminRead, status_code=status.HTTP_201_CREATED)
def create_clothing_item(
    payload: ClothingAdminCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> ClothingAdminRead:
    category = db.get(ClothingCategory, payload.category_id)
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=CATEGORY_NOT_FOUND,
        )

    item = ClothingItem(
        name=payload.name.strip(),
        description=(
            payload.description.strip()
            if payload.description is not None
            else None
        ),
        category_id=category.category_id,
        price=payload.price,
        image_url=payload.image_url.strip(),
        availability_status=payload.availability_status,
        collection_id=payload.collection_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _to_admin_read(item)


@router.patch("/{item_id}", response_model=ClothingAdminRead)
def update_clothing_item(
    item_id: uuid.UUID,
    payload: ClothingAdminUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> ClothingAdminRead:
    item = _get_item(db, item_id)

    updates = payload.model_dump(exclude_unset=True)

    if "category_id" in updates:
        category = db.get(ClothingCategory, updates["category_id"])
        if category is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=CATEGORY_NOT_FOUND,
            )

    for field, value in updates.items():
        setattr(item, field, value)

    try:
        db.commit()
    except IntegrityError as exc:
        # Safety net around the commit; price >= 0 and enum values are
        # already enforced by the schemas, so this mainly guards against a
        # concurrent catalog change racing this write.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Clothing item could not be updated",
        ) from exc

    db.refresh(item)
    return _to_admin_read(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_clothing_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> None:
    item = _get_item(db, item_id)

    # Count references explicitly (RESTRICT FKs): wardrobe ownership and
    # live avatar equipment both block deletion.
    wardrobe_count = db.execute(
        select(func.count(UserWardrobe.wardrobe_id)).where(
            UserWardrobe.item_id == item_id
        )
    ).scalar_one()
    equipped_count = db.execute(
        select(func.count(AvatarEquipment.avatar_id)).where(
            AvatarEquipment.item_id == item_id
        )
    ).scalar_one()

    if wardrobe_count > 0 or equipped_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=REFERENCED_ERROR,
        )

    db.delete(item)
    try:
        db.commit()
    except IntegrityError as exc:
        # Safety net for a concurrent purchase/equip that raced the check.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=REFERENCED_ERROR
        ) from exc


def _get_item(db: Session, item_id: uuid.UUID) -> ClothingItem:
    item = db.execute(
        select(ClothingItem)
        .options(joinedload(ClothingItem.category))
        .where(ClothingItem.item_id == item_id)
    ).scalar_one_or_none()
    if item is None:
        raise _not_found()
    return item


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Clothing item not found"
    )


def _to_admin_read(item: ClothingItem) -> ClothingAdminRead:
    return ClothingAdminRead(
        item_id=item.item_id,
        name=item.name,
        description=item.description,
        category_id=item.category.category_id,
        category_name=item.category.category_name,
        slot=item.category.slot,
        price=item.price,
        image_url=item.image_url,
        availability_status=item.availability_status,
        collection_id=item.collection_id,
        created_at=item.created_at,
    )
