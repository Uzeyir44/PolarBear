"""
Python enums backing native PostgreSQL ENUM columns.

Per the design doc's enum-vs-lookup-table policy (section 1): these stay
plain enums here only because their value sets are closed and stable.
Anything expected to grow (notification types, transaction types,
competition status) is a lookup table instead, modeled with a FK — not
a Python enum — when we get to those batches.
"""
import enum


class AuthProviderType(str, enum.Enum):
    LOCAL = "local"
    GOOGLE = "google"
    APPLE = "apple"


class DevicePlatform(str, enum.Enum):
    IOS = "ios"
    ANDROID = "android"


class AvatarSlot(str, enum.Enum):
    """Shared by avatar_equipment.slot and clothing_categories.slot — a
    category's slot determines which equipment slot its items can fill."""
    HAIR = "hair"
    HAT = "hat"
    TOP = "top"
    BOTTOM = "bottom"
    SHOES = "shoes"
    ACCESSORY = "accessory"


class ClothingAvailability(str, enum.Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UPCOMING = "upcoming"