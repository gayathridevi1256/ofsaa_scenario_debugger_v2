"""Admin audit log route."""

from fastapi import APIRouter, Depends, Query

from app.models.admin import AuditOut
from app.services.audit_service import get_audit_entries
from app.services.auth_service import get_current_admin

router = APIRouter(prefix="/api/admin/audit", tags=["admin"])


@router.get("/", response_model=list[AuditOut])
def get_audit(limit: int = Query(100, le=500), admin: dict = Depends(get_current_admin)):
    entries = get_audit_entries(limit=limit)
    return [AuditOut(**e) for e in entries]
