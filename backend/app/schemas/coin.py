"""
Pydantic schemas for reading the authenticated user's coins:
GET /users/me/coins and GET /users/me/transactions.

Both are OUTPUT-only — nothing here is ever accepted as request input, so
the client cannot influence whose balance/history is returned.

CoinTransactionRead is deliberately NOT a 1:1 mirror of the
coin_transactions table columns. Transactions are the source of truth for
coins, but the ledger stores internal ids. That is useful for the database,
useless for the client, so this schema resolves them to human-meaningful
values:
  - transaction_type is the lookup table's type_name (e.g. "qr_redemption"),
  - direction is the lookup row's CREDIT/DEBIT,
  - `qr` is the joined QR/product context when the transaction was a QR
    redemption (null otherwise).
Fields omitted here — user_id, type_id, and the raw qr_code/wardrobe_entry
relationship objects — are dropped from the response entirely.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CoinBalance(BaseModel):
    """The authenticated user's current coin_balance (mirrors the cached
    value on users.coin_balance)."""

    balance: int


class QRTransactionReference(BaseModel):
    """QR/product context for a qr_redemption transaction: which physical
    code was scanned and which product it belongs to."""

    qr_id: UUID
    code: str
    product_name: str


class CoinTransactionRead(BaseModel):
    """One coin_transactions row as exposed to the client.

    A ledger row references at most one of QR/product, competition,
    wardrobe, or vote — the other reference fields are null. Those
    features aren't implemented yet, so in practice only `qr` is ever
    populated; the nullable ids are there so the schema is ready for them
    without churn later.
    """

    transaction_id: UUID
    amount: int
    balance_after: int
    transaction_type: str
    direction: str
    created_at: datetime
    qr: QRTransactionReference | None = None
    competition_id: UUID | None = None
    wardrobe_id: UUID | None = None
    vote_id: UUID | None = None