"""Admin schemas."""

from pydantic import BaseModel
from app.models.auth import UserOut


class CreateUserRequest(BaseModel):
    username: str
    password: str
    full_name: str = ""
    email: str = ""
    role: str = "analyst"


class AuditOut(BaseModel):
    id: int
    timestamp: str
    user_id: int | None = None
    username: str | None = None
    action: str
    detail: str | None = None
    ip_address: str | None = None
    job_id: str | None = None
