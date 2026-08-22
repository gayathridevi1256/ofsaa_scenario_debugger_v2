"""User management service."""

from app.database.user_repo import create_user, get_user_by_username, get_user_by_id, list_users, deactivate_user
from app.database.audit_repo import audit
from app.services.auth_service import hash_password


def create_new_user(username: str, password: str, full_name: str, email: str, role: str, requester: dict) -> int:
    if get_user_by_username(username):
        raise ValueError(f"User '{username}' already exists")
    pw_hash = hash_password(password)
    user_id = create_user(username, pw_hash, full_name, email, role)
    audit("user_created", username=requester["username"], user_id=requester["id"], detail=f"Created user {username}")
    return user_id


def deactivate_existing_user(user_id: int, requester: dict):
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError(f"User {user_id} not found")
    deactivate_user(user_id)
    audit("user_deactivated", username=requester["username"], user_id=requester["id"], detail=f"Deactivated {user['username']}")


def get_all_users() -> list[dict]:
    return list_users()
