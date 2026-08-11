"""
ORM models.

Batch 1 — core user/authentication: users, auth_providers,
device_tokens, follows.
Batch 2 — avatar & clothing: avatars, clothing_categories,
clothing_items, user_wardrobe, avatar_equipment.

Everything else (QR/coins, competitions, votes, notifications) lands
in later batches and will be added to these exports as it's written.
"""
from .base import Base
from .enums import AuthProviderType, AvatarSlot, ClothingAvailability, DevicePlatform
from .user import User
from .auth_provider import AuthProvider
from .device_token import DeviceToken
from .follow import Follow
from .avatar import Avatar
from .clothing_category import ClothingCategory
from .clothing_item import ClothingItem
from .user_wardrobe import UserWardrobe
from .avatar_equipment import AvatarEquipment

__all__ = [
    "Base",
    "AuthProviderType",
    "AvatarSlot",
    "ClothingAvailability",
    "DevicePlatform",
    "User",
    "AuthProvider",
    "DeviceToken",
    "Follow",
    "Avatar",
    "ClothingCategory",
    "ClothingItem",
    "UserWardrobe",
    "AvatarEquipment",
]