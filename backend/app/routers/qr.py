"""
QR code redemption — Step 3: validate, redeem, AND award coins.

POST /qr/redeem now does the whole flow in ONE database transaction:

  Step 1: lock the code's row (SELECT ... FOR UPDATE) and validate that it
          exists, is ACTIVE, and is not expired.
  Step 2: claim it — status = REDEEMED, redeemed_by_user_id = the
          authenticated user, redeemed_at = now.
  Step 3: credit the user — coin_balance += coin_value, and insert the
          matching coin_transactions ledger row (type = qr_redemption,
          amount = coin_value, balance_after = the new balance, qr_id set).

The three writes are committed together at the end. If anything between the
lock and the commit raises, nothing is committed: get_db() closes the
session, which rolls the whole transaction back. We can never end up with
"QR redeemed but coins not credited" or "coins credited but no ledger row".

Why SELECT ... FOR UPDATE?
A code must be redeemable exactly once, and a naive "load row, check status
in Python, write" can race: two requests can both read status = 'active',
both pass the check, and both award coins. By locking the row with
`SELECT ... FOR UPDATE` we serialize the requests — the first to lock the row
commits its redemption, and the second blocks until the first finishes, then
sees status = 'redeemed' and returns 409. Postgres holds row locks until the
transaction ends (commit OR rollback), which is why the lock covers the
status re-check, the balance update, and the ledger insert.

Why the atomic balance UPDATE?
`coin_balance` is updated with a single
`UPDATE users SET coin_balance = coin_balance + :n RETURNING coin_balance`
rather than read-modify-write in Python. That stops a lost update when the
SAME user redeems two DIFFERENT codes concurrently — a Python-side
`user.coin_balance += n` would let the second request overwrite the first
request's credit using a stale value read before either committed.

Expiration semantics: a code is invalid if its status is EXPIRED, or if
expires_at is set and has already passed. The DB stores timestamps as
`timestamp without time zone` (timezone-naive). redeemed_at is written as
naive UTC to match that convention; treat all stored timestamps as UTC.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import QRCode, QRStatus, CoinTransaction, CoinTransactionType, User
from app.schemas.qr import QRCodeRedeemRequest, QRCodeRedemptionResult

router = APIRouter(prefix="/qr", tags=["qr"])

# type_name of the seeded lookup row in coin_transaction_types.
QR_REDEMPTION_TYPE_NAME = "qr_redemption"


@router.post("/redeem", response_model=QRCodeRedemptionResult)
def redeem_code(
    payload: QRCodeRedeemRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QRCodeRedemptionResult:
    return _redeem_qr(db, payload.code, current_user)


def _redeem_qr(db: Session, code: str, user: User) -> QRCodeRedemptionResult:
    # SELECT ... FOR UPDATE takes an exclusive row lock on the qr_codes row.
    # A concurrent redemption of the same code blocks here until this
    # transaction commits or rolls back, then re-reads the committed row.
    qr = db.execute(
        select(QRCode).where(QRCode.code == code).with_for_update()
    ).scalar_one_or_none()

    if qr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Code not found",
        )

    # We hold the row lock now, so this status check is authoritative: nobody
    # can flip the row between this check and our commit. The lock (not the
    # Python check) is what makes it safe under concurrency.
    if qr.status == QRStatus.REDEEMED:
        raise _already_redeemed()

    if qr.status == QRStatus.EXPIRED or _is_expired(qr):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Code has expired",
        )

    # --- One atomic transaction: claim the QR, credit coins, write the ledger.
    redeemed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    qr.status = QRStatus.REDEEMED
    qr.redeemed_by_user_id = user.user_id
    qr.redeemed_at = redeemed_at

    # Atomic increment; RETURNING gives the authoritative new balance for both
    # the ledger row and the response (see module docstring).
    new_balance = db.execute(
        update(User)
        .where(User.user_id == user.user_id)
        .values(coin_balance=User.coin_balance + qr.coin_value)
        .returning(User.coin_balance)
    ).scalar_one()

    coins_earned = qr.coin_value

    # Seeded by the initial migration; resolved HERE (after the writes above)
    # so that a missing row exercises this endpoint's own rollback path: the
    # claim and the credit are already staged, and the rollback undoes both.
    qr_type_id = db.execute(
        select(CoinTransactionType.type_id).where(
            CoinTransactionType.type_name == QR_REDEMPTION_TYPE_NAME
        )
    ).scalar_one_or_none()
    if qr_type_id is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="QR redemption transaction type is not configured",
        )

    db.add(
        CoinTransaction(
            user_id=user.user_id,
            type_id=qr_type_id,
            amount=coins_earned,
            balance_after=new_balance,
            qr_id=qr.qr_id,
        )
    )

    # One commit for all three writes. If anything above raised, no commit
    # happens and the whole transaction rolls back when the session closes.
    db.commit()

    return QRCodeRedemptionResult(
        message="Code redeemed successfully",
        coins_earned=coins_earned,
        balance=new_balance,
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
