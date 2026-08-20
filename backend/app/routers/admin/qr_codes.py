"""
Admin QR management — list, create, inspect, and manage the status of QR
codes from the internal admin panel.

Covers:
  GET   /admin/qr-codes            paginated list, newest first
  POST  /admin/qr-codes            generate one code on the running product
  GET   /admin/qr-codes/{qr_id}    full administrative detail
  PATCH /admin/qr-codes/{qr_id}    deactivate / reactivate an unredeemed code

Every endpoint depends on get_current_admin(), so only administrators
reach them; normal users get 403 and unauthenticated callers 401.

Design notes
------------
- REDEEMED codes are immutable. They are audit history (who redeemed
  what, when), so the admin API never lets an operator edit one — it has
  no allowed transitions in _STATUS_TRANSITIONS.
- "Deactivating" a code means moving it ACTIVE -> EXPIRED; the existing
  status enum has no separate inactive value, and expiry is the model's
  own definition of "no longer redeemable". EXPIRED -> ACTIVE (reactivate)
  is allowed so an operator can correct a mistaken deactivation. The DB
  CHECK constraint guarantees only REDEEMED rows carry redemption fields,
  so ACTIVE <-> EXPIRED never touches them.
- The code itself is generated here (it does not exist anywhere else in
  the codebase — codes were previously inserted by hand). It is random
  and validated against the unique constraint so the admin panel cannot
  produce a duplicate or predictable code.
"""
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_admin
from app.models import Product, QRCode, QRStatus, User
from app.schemas.admin_qr import (
    QRAdminCreate,
    QRAdminList,
    QRAdminProduct,
    QRAdminProductList,
    QRAdminRead,
    QRAdminUpdate,
)

router = APIRouter(tags=["admin"])

# Prefix for every generated code. Codes are random per code; uniqueness is
# enforced by the DB unique index on qr_codes.code.
CODE_PREFIX = "PB-"

# Legal administrative status changes. Only unredeemed codes can be
# toggled; REDEEMED is terminal (audit trail) and has no row in the map.
_ALLOWED_TRANSITIONS: dict[QRStatus, set[QRStatus]] = {
    QRStatus.ACTIVE: {QRStatus.EXPIRED},   # deactivate a live code
    QRStatus.EXPIRED: {QRStatus.ACTIVE},   # reactivate (operator correction)
}


@router.get("/products", response_model=QRAdminProductList)
def list_products(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> QRAdminProductList:
    """Read-only list of products for the QR creation form. Not product
    management — just the choices an administrator needs when generating
    a code. Add a full admin products module later, separately."""
    products = db.execute(select(Product).order_by(Product.name)).scalars().all()
    return QRAdminProductList(
        items=[
            QRAdminProduct(product_id=p.product_id, name=p.name, sku=p.sku)
            for p in products
        ],
        total=len(products),
    )


@router.get("", response_model=QRAdminList)
def list_qr_codes(
    status_filter: QRStatus | None = Query(
        default=None,
        alias="status",
        description="Filter by QR status",
    ),
    product_id: uuid.UUID | None = Query(
        default=None,
        description="Filter by product UUID",
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="How many QR codes to return (1-100)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="How many QR codes to skip before returning results",
    ),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> QRAdminList:
    conditions = []
    if status_filter is not None:
        conditions.append(QRCode.status == status_filter)
    if product_id is not None:
        conditions.append(QRCode.product_id == product_id)

    total = db.execute(select(func.count(QRCode.qr_id)).where(*conditions)).scalar_one()
    rows = db.execute(
        select(QRCode)
        .where(*conditions)
        .order_by(QRCode.created_at.desc(), QRCode.qr_id.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    return QRAdminList(
        items=[_to_admin_read(qr) for qr in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=QRAdminRead, status_code=status.HTTP_201_CREATED)
def create_qr_code(
    payload: QRAdminCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> QRCode:
    if db.get(Product, payload.product_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    qr = _insert_with_unique_code(db, payload)
    db.commit()
    db.refresh(qr)
    return qr


@router.get("/{qr_id}", response_model=QRAdminRead)
def get_qr_detail(
    qr_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> QRCode:
    qr = _get_qr(db, qr_id)
    return qr


@router.patch("/{qr_id}", response_model=QRAdminRead)
def update_qr_status(
    qr_id: uuid.UUID,
    payload: QRAdminUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> QRCode:
    qr = _get_qr(db, qr_id)

    allowed = _ALLOWED_TRANSITIONS.get(qr.status, set())
    if payload.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Invalid status transition: {qr.status.name} -> "
                f"{payload.status.name} (redeemed QR codes cannot be modified)"
                if qr.status == QRStatus.REDEEMED
                else f"Invalid status transition: {qr.status.name} -> {payload.status.name}"
            ),
        )

    qr.status = payload.status
    db.commit()
    db.refresh(qr)
    return qr


def _get_qr(db: Session, qr_id: uuid.UUID) -> QRCode:
    qr = db.get(QRCode, qr_id)
    if qr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="QR code not found",
        )
    return qr


def _insert_with_unique_code(db: Session, payload: QRAdminCreate) -> QRCode:
    """Insert a QR with a fresh random code, retrying on the (astronomically
    unlikely) unique-constraint collision."""
    for _ in range(5):
        qr = QRCode(
            code=f"{CODE_PREFIX}{secrets.token_hex(8).upper()}",
            product_id=payload.product_id,
            coin_value=payload.coin_value,
            status=QRStatus.ACTIVE,
            expires_at=payload.expires_at,
        )
        db.add(qr)
        try:
            db.flush()
            return qr
        except IntegrityError:
            db.rollback()
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Could not allocate a unique QR code",
    )


def _to_admin_read(qr: QRCode) -> QRAdminRead:
    redeemed_by = None
    if qr.redeemed_by is not None:
        redeemed_by = {
            "user_id": qr.redeemed_by.user_id,
            "username": qr.redeemed_by.username,
        }
    return QRAdminRead(
        qr_id=qr.qr_id,
        code=qr.code,
        product={
            "product_id": qr.product.product_id,
            "name": qr.product.name,
            "sku": qr.product.sku,
        },
        coin_value=qr.coin_value,
        status=qr.status,
        redeemed_by=redeemed_by,
        redeemed_at=qr.redeemed_at,
        expires_at=qr.expires_at,
        created_at=qr.created_at,
    )