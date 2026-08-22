"""Audit service."""

from app.database.audit_repo import audit, get_audit_log


def get_audit_entries(limit: int = 100, user_id: int = None) -> list[dict]:
    return get_audit_log(limit=limit, user_id=user_id)
