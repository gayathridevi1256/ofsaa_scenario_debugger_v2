"""Database schema — creates all tables on first run."""

import logging
from app.database.connection import get_db
from app.config import DB_PATH

logger = logging.getLogger(__name__)


def _add_column_if_missing(db, table: str, column: str, col_type: str):
    """Lightweight migration for existing DB files created before this column existed."""
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def init_db():
    logger.info("Initialising database at: %s", DB_PATH)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                full_name     TEXT    NOT NULL DEFAULT '',
                email         TEXT    NOT NULL DEFAULT '',
                role          TEXT    NOT NULL DEFAULT 'analyst',
                is_active     INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT    NOT NULL,
                last_login    TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id           TEXT    NOT NULL UNIQUE,
                user_id          INTEGER NOT NULL REFERENCES users(id),
                log_filename     TEXT    NOT NULL,
                scenario_name    TEXT,
                batch_date       TEXT,
                status           TEXT    NOT NULL DEFAULT 'pending',
                current_step     TEXT,
                started_at       TEXT    NOT NULL,
                completed_at     TEXT,
                error_message    TEXT,
                output_dir       TEXT,
                alerts_generated INTEGER DEFAULT 0,
                root_cause       TEXT,
                result_json      TEXT,
                cte_results_json TEXT,
                ai_recommendation TEXT
            )
        """)
        _add_column_if_missing(db, "jobs", "ai_recommendation", "TEXT")

        db.execute("""
            CREATE TABLE IF NOT EXISTS job_steps (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      TEXT    NOT NULL REFERENCES jobs(job_id),
                step_name   TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'pending',
                started_at  TEXT,
                completed_at TEXT,
                output      TEXT,
                error       TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  TEXT    NOT NULL,
                user_id    INTEGER REFERENCES users(id),
                username   TEXT,
                action     TEXT    NOT NULL,
                detail     TEXT,
                ip_address TEXT,
                job_id     TEXT
            )
        """)

    logger.info("Database initialised successfully")
