"""
Clothing shop — browsing (Phase 3, step 1) and purchasing (Phase 3, step 2).

GET /clothing lists the purchasable catalog for an authenticated user,
paginated with limit/offset, with an optional category filter.

POST /clothing/{item_id}/purchase buys one catalog item for the
authenticated user: it debits the item's price from users.coin_balance,
creates the permanent user_wardrobe ownership record, and writes the
matching coin_transactions ledger row — all in ONE database transaction.

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
  The purchase endpoint enforces the same rule: only AVAILABLE items can
  be bought, regardless of what the client saw while browsing.
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
- Price integrity: the purchase price comes ONLY from the
  clothing_items row loaded inside the endpoint. There is no request
  body field a client could set, so the server-charged price can never
  diverge from the catalog price.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import (
    ClothingAvailability,
    ClothingCategory,
    ClothingItem,
    CoinTransaction,
    CoinTransactionType,
    User,
    UserWardrobe,
)
from app.schemas.clothing import (
    ClothingCategoryRef,
    ClothingItemList,
    ClothingItemRead,
    ClothingPurchaseResult,
)

router = APIRouter(prefix="/clothing", tags=["clothing"])

# type_name of the seeded lookup row in coin_transaction_types.
CLOTHING_PURCHASE_TYPE_NAME = "clothing_purchase"


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


@router.post("/{item_id}/purchase", response_model=ClothingPurchaseResult)
def purchase_clothing_item(
    item_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClothingPurchaseResult:
    # The URL's {item_id} is parsed by FastAPI as a uuid.UUID (malformed
    # values -> 422) and is the ONLY client input. There is no request
    # body, so the client can never influence the price, the ledger
    # amount, or the balance — everything below is read from the DB.
    return _purchase_item(db, item_id, current_user)


def _purchase_item(db: Session, item_id: uuid.UUID, user: User) -> ClothingPurchaseResult:
    """Buy one clothing item for `user` as a single atomic transaction.

    Concurrency strategy (mirrors the QR redemption flow in qr.py):

    1. LOCK THE USER ROW first (`SELECT ... FOR UPDATE`). The contended
       resource in a purchase is the buyer's coin balance, so all of a
       user's debits are serialized on their own row: two concurrent
       purchases by the same user block here until the first commits,
       then the second re-reads the post-debit balance. This makes both
       races safe:
         - same item twice  -> the loser sees the winner's wardrobe row
           in the ownership check and gets 409;
         - two different items that together exceed the balance -> the
           loser fails the balance check with 400 instead of driving
           coin_balance negative.
       Postgres holds the row lock until COMMIT/ROLLBACK, so the lock
       covers every check and write below.
    2. The balance deduction itself is still an atomic guarded UPDATE
       (`SET coin_balance = coin_balance - :price ... WHERE coin_balance
       >= :price RETURNING coin_balance`) — the same pattern qr.py uses.
       Belt and braces: even without the lock this statement cannot
       produce a negative balance or a lost update, because Postgres
       re-evaluates the WHERE guard against the committed value after
       any concurrent writer's lock releases.
    3. Duplicate ownership keeps BOTH protections: the app-level check
       below turns the common case into a friendly 409, and the
       uq_user_wardrobe_no_duplicate_purchase unique constraint remains
       the final authority against a race — an IntegrityError from it is
       caught, rolled back, and reported as the same 409.

    Everything (lock, checks, debit, wardrobe insert, ledger insert)
    commits together; any failure before commit rolls ALL of it back via
    get_db() closing the session, so there is never a state where coins
    moved without a wardrobe row, a wardrobe row exists without payment,
    or a ledger row's balance_after disagrees with users.coin_balance.
    """
    # --- Lock the buyer's row: serializes this user's concurrent debits ---
    locked_user = db.execute(
        select(User).where(User.user_id == user.user_id).with_for_update()
    ).scalar_one()

    # --- Validate the item (no lock needed: price/availability are
    #     admin-owned catalog fields; worst case an admin edits the item
    #     mid-purchase, which is acceptable for a shop purchase). ---
    item = db.execute(
        select(ClothingItem)
        .options(joinedload(ClothingItem.category))
        .where(ClothingItem.item_id == item_id)
    ).scalar_one_or_none()

    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clothing item not found",
        )

    if item.availability_status != ClothingAvailability.AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Clothing item is not available for purchase",
        )

    # --- Ownership pre-check: friendly 409 for the common case. We hold
    #     the user-row lock, so no concurrent purchase by this user can
    #     slip a duplicate past this check; the unique constraint below
    #     stays as the final protection regardless. ---
    already_owned = db.execute(
        select(UserWardrobe.wardrobe_id).where(
            UserWardrobe.user_id == user.user_id,
            UserWardrobe.item_id == item.item_id,
        )
    ).scalar_one_or_none()
    if already_owned is not None:
        raise _already_owned()

    # --- Balance check under the lock: authoritative read of coin_balance.
    #     users.ck_users_coin_balance_non_negative backs this up at the DB. ---
    if locked_user.coin_balance < item.price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient coin balance",
        )

    try:
        # --- Debit: atomic guarded UPDATE; RETURNING gives the
        #     authoritative new balance for both the ledger row and the
        #     response (see module docstring / qr.py for why this beats
        #     read-modify-write in Python). ---
        new_balance = db.execute(
            update(User)
            .where(
                User.user_id == user.user_id,
                User.coin_balance >= item.price,
            )
            .values(coin_balance=User.coin_balance - item.price)
            .returning(User.coin_balance)
        ).scalar_one_or_none()

        if new_balance is None:
            # Only reachable if the balance changed between our locked
            # read and this statement — treat it as insufficient funds.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient coin balance",
            )

        # --- Permanent ownership record. flush() assigns the
        #     server-generated wardrobe_id now, so the ledger row can
        #     reference it in the same transaction. ---
        wardrobe = UserWardrobe(user_id=user.user_id, item_id=item.item_id)
        db.add(wardrobe)
        db.flush()

        # Seeded by the initial migration; resolved HERE (after the writes
        # above) so that a missing row exercises this endpoint's own
        # rollback path: the debit and the ownership insert are already
        # staged, and the rollback undoes both.
        purchase_type_id = db.execute(
            select(CoinTransactionType.type_id).where(
                CoinTransactionType.type_name == CLOTHING_PURCHASE_TYPE_NAME
            )
        ).scalar_one_or_none()
        if purchase_type_id is None:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Clothing purchase transaction type is not configured",
            )

        # --- Ledger row: DEBIT of exactly the catalog price, snapshotting
        #     the post-debit balance. amount is signed negative per the
        #     coin_transactions convention (positive = credit). ---
        transaction = CoinTransaction(
            user_id=user.user_id,
            type_id=purchase_type_id,
            amount=-item.price,
            balance_after=new_balance,
            wardrobe_id=wardrobe.wardrobe_id,
        )
        db.add(transaction)

        # One commit for the debit + ownership + ledger. If anything above
        # raised, no commit happens and the whole transaction rolls back
        # when the session closes.
        db.commit()
    except IntegrityError as exc:
        # Races only code that bypasses the user-row lock (e.g. manual SQL);
        # uq_user_wardrobe_no_duplicate_purchase fired. Roll back the debit
        # and everything else staged in this transaction, then report the
        # same business error the pre-check would have produced.
        db.rollback()
        raise _already_owned() from exc

    return ClothingPurchaseResult(
        message="Item purchased successfully",
        wardrobe_id=wardrobe.wardrobe_id,
        item=_to_item_read(item),
        amount_spent=item.price,
        remaining_balance=new_balance,
        transaction_id=transaction.transaction_id,
    )


def _already_owned() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="You already own this clothing item",
    )
