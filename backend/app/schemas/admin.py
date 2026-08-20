"""
Schemas for admin-console concerns that span modules.

AdminUserRead is the response of GET /admin/me — the identity of the
currently authenticated administrator, shown in the admin sidebar. It is
deliberately narrow: only what the operator needs to see who they are.
"""
import uuid

from pydantic import BaseModel, ConfigDict


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    username: str
    email: str
    is_admin: bool