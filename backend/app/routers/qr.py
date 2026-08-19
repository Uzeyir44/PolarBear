"""
QR code redemption — Step 2: validate a code AND mark it redeemed.

POST /qr/redeem now does the full first half of redemption in one shot:

  Step 1: find the code, check it exists, is ACTIVE, and is not expired.
  Step 2: atomically claim it — status = REDEEMED, redeemed_by_user_id =
          the authenticated user, redeemed_at = now. Committed immediately.

Coins are deliberately NOT awarded here: no coin_balance change, no
coin_transaction row. That is the next step.

Double-redemption protection (two layers):
  1. Application pre-check: if the row we loaded is already REDEEMED we
     return 409 before writing anything.
  2. Atomic UPDATE ... WHERE status = 'active': this guard closes the race
     where two concurrent requests both pass the pre-check before either
     commits. Postgres locks the row at UPDATE-time; the second UPDATE
     matches zero rows (rowcount == 0) and we return 409. Without this a
     single code could be redeemed twice under concurrency.

Expiration semantics: a code is invalid if its status is EXPIRED, or if
expires_at is set and has already passed. The DB stores timestamps as
`timestamp without time zone` (timezone-naive). redeemed_at is written as
naive UTC to match that convention (see Step-1 notes); treat all stored
timestamps as UTC.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import QRCode, QRStatus, User
from app.schemas.qr import QRCodeRedeemRequest, QRCodeRedemptionResult

router = APIRouter(prefix="/qr", tags=["qr"])


@router.post("/redeem", response_model=QRCodeRedemptionResult)
def redeem_code(
    payload: QRCodeRedeemRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QRCodeRedemptionResult:
    qr = db.execute(
        select(QRCode).where(QRCode.code == payload.code)
    ).scalar_one_or_none()

    if qr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Code not found",
        )

    # Friendly pre-checks so a clearly unusable code fails before any write.
    if qr.status == QRStatus.REDEEMED:
        raise _already_redeemed()

    # EXPIRED status OR an ACTIVE code whose expires_at has passed.
    if qr.status == QRStatus.EXPIRED or _is_expired(qr):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Code has expired",
        )

    # Claim the code atomically. The `WHERE status = 'active'` is the real
    # anti-double-redeem guard: a concurrent request that already flipped
    # this row to REDEEMED will match zero rows here.
    redeemed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    result = db.execute(
        update(QRCode)
        .where(QRCode.qr_id == qr.qr_id, QRCode.status == QRStatus.ACTIVE)
        .values(
            status=QRStatus.REDEEMED,
            redeemed_by_user_id=current_user.user_id,
            redeemed_at=redeemed_at,
        )
    )

    if result.rowcount == 0:
        # Lost the race — someone else redeemed it between our pre-check and
        # the UPDATE. Discard the (empty) transaction and report 409.
        db.rollback()
        raise _already_redeemed()

    db.commit()
    # Reload the row so the response reflects what is actually in the DB
    # (the Core UPDATE above doesn't refresh the ORM object by itself).
    db.refresh(qr)

    return QRCodeRedemptionResult(
        message="Code redeemed successfully",
        qr_id=qr.qr_id,
        redeemed_at=qr.redeemed_at,
    )


def _is_expired(qr: QRCode) -> bool:
    if qr.expires_at is None:
        return False
    expires_at = qr.expires_at
    if expires_at.tzinfo is None:
        # DB column is `timestamp without time zone`; treat stored values
        # as UTC so the comparison with an aware clock is well-defined.
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < datetime.now(timezone.utc)


def _already_redeemed() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Code has already been redeemed",
    )