"""
Pydantic schemas for QR code redemption.

QRCodeRedeemRequest is the input to POST /qr/redeem. It carries only the
submitted code — nothing else, so the client can't influence which QR row
is looked up beyond the code itself.

QRCodeRedemptionResult is the success response after a code is redeemed
AND its coins awarded. It returns only the message, how many coins the
code was worth, and the user's new balance. Internal fields (qr_id,
product_id, status, redeemed_by_user_id, redeemed_at, expires_at, ...)
are NOT exposed.
"""
from pydantic import BaseModel, Field


class QRCodeRedeemRequest(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=64,
        description="The code printed on the QR/barcode, e.g. COLA-123456",
    )


class QRCodeRedemptionResult(BaseModel):
    """Step-3 outcome: the code was redeemed AND its coins were credited."""

    message: str
    coins_earned: int
    balance: int
