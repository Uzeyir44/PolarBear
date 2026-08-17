"""
User routes.

/me returns the profile of the currently authenticated user. It is the
first endpoint protected by get_current_user() and serves as the pattern
for every future endpoint that requires a logged-in user.
"""
from fastapi import APIRouter, Depends

from app.dependencies import get_current_user
from app.models import User
from app.schemas.user import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
def read_current_user(current_user: User = Depends(get_current_user)) -> User:
    return current_user