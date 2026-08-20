"""
Pydantic schemas for the admin QR management module.

Separates input (what an administrator sends) from output (what the API
returns). Output schemas define exactly which fields leave the server:
the list/detail responses expose the nested product and — for redeemed
codes — who redeemed them and when, but never internal housekeeping.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import QRStatus


class QRAdminProduct(BaseModel):
    """Minimal product info nested inside QR admin responses."""

    model_config = ConfigDict(from_attributes=True)

    product_id: uuid.UUID
    name: str
    sku: str


class QRAdminRedeemedBy(BaseModel):
    """Who redeemed the code, for the admin view. Only present when the
    code has actually been redeemed."""

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    username: str


class QRAdminCreate(BaseModel):
    """Body of POST /admin/qr-codes.

    Every generated QR starts ACTIVE (the server decides status — the
    client cannot). coin_value must be a positive integer (mirrors the
    DB CHECK constraint ck_qr_codes_coin_value_positive). expires_at is
    optional; when set it is the moment the code becomes invalid for
    redemption.
    """

    product_id: uuid.UUID
    coin_value: int = Field(gt=0, description="Coins awarded on redemption (must be > 0)")
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiry; once reached the code cannot be redeemed",
    )


class QRAdminProductList(BaseModel):
    """Read-only product choices for the QR creation form. There is no
    product management here — this is the bare minimum an administrator
    needs to target a QR code at an existing product."""

    items: list[QRAdminProduct]
    total: int


class QRAdminUpdate(BaseModel):
    """Body of PATCH /admin/qr-codes/{qr_id}.

    Currently supports exactly one safe administrative action: toggling
    the status of an UNREDEEMED code between ACTIVE and EXPIRED
    (deactivate / reactivate). Whether a transition is legal is resolved
    server-side — this schema cannot express anything else.
    """

    status: QRStatus


class QRAdminRead(BaseModel):
    """Full administrative view of one QR code.

    For redeemed codes redeemed_by/redeemed_at tell the admin WHO used the
    code and WHEN — that is the audit information the admin module needs.
    """

    model_config = ConfigDict(from_attributes=True)

    qr_id: uuid.UUID
    code: str
    product: QRAdminProduct
    coin_value: int
    status: QRStatus
    redeemed_by: QRAdminRedeemedBy | None = None
    redeemed_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime


class QRAdminList(BaseModel):
    """Paginated response of GET /admin/qr-codes.

    total is the count of QR codes matching the current filters, so the
    admin UI can page through a stable result set. items, limit and
    offset describe the returned page.
    """

    items: list[QRAdminRead]
    total: int
    limit: int
    offset: int