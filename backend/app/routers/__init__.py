from .auth import router as auth_router
from .clothing import router as clothing_router
from .qr import router as qr_router
from .users import router as users_router
from .wardrobe import router as wardrobe_router
from .admin import router as admin_router

__all__ = ["auth_router", "clothing_router", "qr_router", "users_router", "wardrobe_router", "admin_router"]
