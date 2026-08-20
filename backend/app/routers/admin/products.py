"""
Admin product management — list, inspect, create, update, and safely
delete products.

Covers:
  GET    /admin/products                 paginated list, newest first, with
                                         name/SKU search and qr_code_count
  GET    /admin/products/{product_id}    administrative detail + 404
  POST   /admin/products                 create (SKU unique)
  PATCH  /admin/products/{product_id}    update name/SKU (SKU unique)
  DELETE /admin/products/{product_id}    delete ONLY if unreferenced by QR

Every endpoint depends on get_current_admin(), so only administrators
reach them; normal users get 403 and unauthenticated callers 401.

Design notes
------------
- product_id and created_at are database-owned. The create/update schemas
  contain no fields for them, so a client can never set or change them.
- The SKU column has a UNIQUE constraint, so duplicates are rejected with
  409. To match the existing register endpoint's convention we pre-check
  for a friendly message and fall back to catching the DB IntegrityError
  (covers the race between the check and the commit).
- Deletion is deliberately conservative. qr_codes.product_id has an
  ondelete=RESTRICT FK, and redeemed QR codes are audit history — a
  referenced product must NOT be deleted. The endpoint checks the
  reference count first and returns 409 "Product cannot be deleted
  because it is referenced by QR codes." only if unreferenced does it
  delete. This respects the existing schema; no soft-delete column needed.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_admin
from app.models import Product, QRCode, User
from app.routers.users import escape_like
from app.schemas.product import ProductAdminCreate, ProductAdminList, ProductAdminRead, ProductAdminUpdate

router = APIRouter(tags=["admin"])

REFERENCED_ERROR = "Product cannot be deleted because it is referenced by QR codes."
SKU_TAKEN_ERROR = "A product with this SKU already exists"


@router.get("", response_model=ProductAdminList)
def list_products(
    q: str | None = Query(
        default=None,
        max_length=50,
        description="Search fragment for product name or SKU (partial, case-insensitive)",
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="How many products to return (1-100)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="How many products to skip before returning results",
    ),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> ProductAdminList:
    conditions = []
    if q:
        # Reuse the shared ILIKE escaper from /users/search for consistency.
        pattern = f"%{escape_like(q.strip())}%"
        conditions.append(
            or_(
                Product.name.ilike(pattern, escape="\\"),
                Product.sku.ilike(pattern, escape="\\"),
            )
        )

    total = db.execute(select(func.count(Product.product_id)).where(*conditions)).scalar_one()
    rows = db.execute(
        select(Product, func.count(QRCode.qr_id))
        .outerjoin(QRCode, QRCode.product_id == Product.product_id)
        .where(*conditions)
        .group_by(Product.product_id)
        .order_by(Product.created_at.desc(), Product.product_id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return ProductAdminList(
        items=[_to_admin_read(product, count) for product, count in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{product_id}", response_model=ProductAdminRead)
def get_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> ProductAdminRead:
    product = db.get(Product, product_id)
    if product is None:
        raise _not_found()
    qr_count = db.execute(
        select(func.count(QRCode.qr_id)).where(QRCode.product_id == product_id)
    ).scalar_one()
    return _to_admin_read(product, qr_count)


@router.post("", response_model=ProductAdminRead, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductAdminCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> ProductAdminRead:
    _ensure_sku_free(db, payload.sku)

    product = Product(name=payload.name.strip(), sku=payload.sku.strip())
    db.add(product)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _sku_taken() from exc

    db.refresh(product)
    return _to_admin_read(product, qr_code_count=0)


@router.patch("/{product_id}", response_model=ProductAdminRead)
def update_product(
    product_id: uuid.UUID,
    payload: ProductAdminUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> ProductAdminRead:
    product = db.get(Product, product_id)
    if product is None:
        raise _not_found()

    updates = payload.model_dump(exclude_unset=True)

    if "sku" in updates and updates["sku"].strip() != product.sku:
        _ensure_sku_free(db, updates["sku"].strip())

    for field, value in updates.items():
        setattr(product, field, value.strip() if isinstance(value, str) else value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _sku_taken() from exc

    db.refresh(product)
    qr_count = db.execute(
        select(func.count(QRCode.qr_id)).where(QRCode.product_id == product_id)
    ).scalar_one()
    return _to_admin_read(product, qr_count)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> None:
    product = db.get(Product, product_id)
    if product is None:
        raise _not_found()

    qr_count = db.execute(
        select(func.count(QRCode.qr_id)).where(QRCode.product_id == product_id)
    ).scalar_one()
    if qr_count > 0:
        # RESTRICT FK + audit-history QR rows mean deletion is unsafe.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=REFERENCED_ERROR)

    db.delete(product)
    try:
        db.commit()
    except IntegrityError as exc:
        # Safety net for any concurrent QR insert that raced the check.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=REFERENCED_ERROR
        ) from exc


def _ensure_sku_free(db: Session, sku: str) -> None:
    taken = db.execute(
        select(Product.product_id).where(Product.sku == sku)
    ).scalar_one_or_none()
    if taken is not None:
        raise _sku_taken()


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")


def _sku_taken() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SKU_TAKEN_ERROR)


def _to_admin_read(product: Product, qr_code_count: int) -> ProductAdminRead:
    return ProductAdminRead(
        product_id=product.product_id,
        name=product.name,
        sku=product.sku,
        created_at=product.created_at,
        qr_code_count=qr_code_count,
    )