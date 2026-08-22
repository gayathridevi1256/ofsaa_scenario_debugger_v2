"""Audit log repository."""

from datetime import datetime, timezone
from app.database.connection import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit(action: str, username: str = None, user_id: int = None, detail: str = None, ip_address: str = None, job_id: str = None):
    with get_db() as db:
        db.execute(
            "INSERT INTO audit_log (timestamp, user_id, username, action, detail, ip_address, job_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now(), user_id, username, action, detail, ip_address, job_id),
        )


def get_audit_log(limit: int = 100, user_id: int = None) -> list[dict]:
    with get_db() as db:
        if user_id:
            rows = db.execute(
                "SELECT * FROM audit_log WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?", (user_id, limit)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]
