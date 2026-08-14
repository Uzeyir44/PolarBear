"""
ORM models — complete schema, all 19 tables from the design doc.

Batch 1 — core user/authentication: users, auth_providers,
device_tokens, follows.
Batch 2 — avatar & clothing: avatars, clothing_categories,
clothing_items, user_wardrobe, avatar_equipment.
Batch 3 — QR/coins: products, qr_codes, coin_transaction_types,
coin_transactions.
Batch 4 — competitions: competition_requests, competition_status,
competitions, votes.
Batch 5 — notifications: notification_types, notifications.

Two Postgres triggers ship alongside the ORM definitions (see
competition.py and vote.py) — they install automatically via
`Base.metadata.create_all(engine)` through SQLAlchemy DDL events, but
haven't been executed against a real Postgres instance yet. Verify
them against an actual database before relying on them in production.
"""
from app.core.database import Base
from .enums import (
    AuthProviderType,
    AvatarSlot,
    ClothingAvailability,
    CompetitionRequestStatus,
    DevicePlatform,
    QRStatus,
    TransactionDirection,
)
from .user import User
from .auth_provider import AuthProvider
from .device_token import DeviceToken
from .follow import Follow
from .avatar import Avatar
from .clothing_category import ClothingCategory
from .clothing_item import ClothingItem
from .user_wardrobe import UserWardrobe
from .avatar_equipment import AvatarEquipment
from .product import Product
from .qr_code import QRCode
from .coin_transaction_type import CoinTransactionType
from .coin_transaction import CoinTransaction
from .competition_status import CompetitionStatus
from .competition_request import CompetitionRequest
from .competition import Competition
from .vote import Vote
from .notification_type import NotificationType
from .notification import Notification

__all__ = [
    "Base",
    "AuthProviderType",
    "AvatarSlot",
    "ClothingAvailability",
    "CompetitionRequestStatus",
    "DevicePlatform",
    "QRStatus",
    "TransactionDirection",
    "User",
    "AuthProvider",
    "DeviceToken",
    "Follow",
    "Avatar",
    "ClothingCategory",
    "ClothingItem",
    "UserWardrobe",
    "AvatarEquipment",
    "Product",
    "QRCode",
    "CoinTransactionType",
    "CoinTransaction",
    "CompetitionStatus",
    "CompetitionRequest",
    "Competition",
    "Vote",
    "NotificationType",
    "Notification",
]