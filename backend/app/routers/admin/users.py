"""Admin user management routes."""

from fastapi import APIRouter, Depends, HTTPException

from app.models.admin import CreateUserRequest
from app.models.auth import UserOut
from app.services.user_service import create_new_user, deactivate_existing_user, get_all_users
from app.services.auth_service import get_current_admin

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


@router.get("/", response_model=list[UserOut])
def list_users(admin: dict = Depends(get_current_admin)):
    users = get_all_users()
    return [UserOut(**u) for u in users]


@router.post("/", response_model=UserOut)
def create_user(req: CreateUserRequest, admin: dict = Depends(get_current_admin)):
    try:
        uid = create_new_user(req.username, req.password, req.full_name, req.email, req.role, admin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    from app.database.user_repo import get_user_by_id
    user = get_user_by_id(uid)
    return UserOut(**user)


@router.delete("/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(get_current_admin)):
    try:
        deactivate_existing_user(user_id, admin)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
