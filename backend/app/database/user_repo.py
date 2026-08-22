"""User repository — CRUD queries for users table."""

from datetime import datetime, timezone
from app.database.connection import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_user(username: str, password_hash: str, full_name: str, email: str, role: str = "analyst") -> int:
    now = _now()
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO users (username, password_hash, full_name, email, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (username, password_hash, full_name, email, role, now),
        )
        return cursor.lastrowid


def get_user_by_username(username: str) -> dict | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (user_id,)).fetchone()
    return dict(row) if row else None


def update_last_login(user_id: int):
    with get_db() as db:
        db.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), user_id))


def list_users() -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT id, username, full_name, email, role, is_active, created_at, last_login FROM users ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def deactivate_user(user_id: int):
    with get_db() as db:
        db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))


def ensure_admin_exists():
    import bcrypt
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count == 0:
        pw_hash = bcrypt.hashpw("changeme123".encode(), bcrypt.gensalt()).decode()
        create_user("admin", pw_hash, "Administrator", "admin@localhost", "admin")
