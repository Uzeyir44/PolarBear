"""
Admin console router — public namespace for all internal management APIs.

GET /admin/me reports the currently authenticated administrator's own
identity. Module routers (QR Codes and Users today; Clothing, Products,
Competitions, Notifications later) are sub-routers mounted under the same
/admin prefix, so the admin surface grows without touching existing
routes. get_current_admin() is enforced once at the package level via the
router `dependencies` list — every endpoint beneath /admin inherits it.
"""
from fastapi import APIRouter, Depends

from app.dependencies import get_current_admin
from app.models import User
from app.schemas.admin import AdminUserRead
from . import qr_codes, users

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])


@router.get("/me", response_model=AdminUserRead)
def admin_me(current_admin: User = Depends(get_current_admin)) -> User:
    return current_admin


router.include_router(qr_codes.router, prefix="/qr-codes")
router.include_router(users.router, prefix="/users")