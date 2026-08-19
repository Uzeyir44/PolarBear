"""
Pydantic schemas for QR code redemption.

QRCodeRedeemRequest is the input to POST /qr/redeem. It carries only the
submitted code — nothing else, so the client can't influence which QR row
is looked up beyond the code itself.

QRCodeRedemptionResult is the success response after a code is marked
redeemed. It returns only the message, the redeemed QR's id, and the
redemption timestamp. Internal fields (product_id, status,
redeemed_by_user_id, expires_at, ...) are NOT exposed. Coin-award
responses will be added by the coin step.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class QRCodeRedeemRequest(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=64,
        description="The code printed on the QR/barcode, e.g. COLA-123456",
    )


class QRCodeRedemptionResult(BaseModel):
    """Step-2 outcome: the code was successfully marked as redeemed."""

    message: str
    qr_id: UUID
    redeemed_at: datetime
