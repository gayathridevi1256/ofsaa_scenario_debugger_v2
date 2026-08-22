"""Job repository — CRUD queries for jobs and job_steps tables."""

from datetime import datetime, timezone
from app.database.connection import get_db

_STEP_NAMES = ["log_reader", "set_batch_date", "sql_executer", "cte_parser", "cte_executer", "sql_diagnostics"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(job_id: str, user_id: int, log_filename: str) -> dict:
    now = _now()
    with get_db() as db:
        db.execute("INSERT INTO jobs (job_id, user_id, log_filename, status, started_at) VALUES (?, ?, ?, 'pending', ?)",
                   (job_id, user_id, log_filename, now))
        for step in _STEP_NAMES:
            db.execute("INSERT INTO job_steps (job_id, step_name, status) VALUES (?, ?, 'pending')", (job_id, step))
    return get_job(job_id)


def get_job(job_id: str) -> dict | None:
    with get_db() as db:
        job_row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not job_row:
            return None
        step_rows = db.execute("SELECT * FROM job_steps WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
    job = dict(job_row)
    job["steps"] = [dict(s) for s in step_rows]
    return job


def list_jobs(user_id: int = None, limit: int = 50) -> list[dict]:
    with get_db() as db:
        if user_id:
            rows = db.execute(
                "SELECT j.*, u.username FROM jobs j JOIN users u ON j.user_id = u.id WHERE j.user_id = ? ORDER BY j.started_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT j.*, u.username FROM jobs j JOIN users u ON j.user_id = u.id ORDER BY j.started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def update_job_status(job_id: str, status: str, **kwargs):
    fields = ["status = ?"]
    values = [status]
    for key, val in kwargs.items():
        if val is not None:
            fields.append(f"{key} = ?")
            values.append(val)
    if status in ("completed", "failed"):
        fields.append("completed_at = ?")
        values.append(_now())
    values.append(job_id)
    with get_db() as db:
        db.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?", values)


def update_step_status(job_id: str, step_name: str, status: str, output: str = None, error: str = None):
    fields = ["status = ?"]
    values = [status]
    if status == "running":
        fields.append("started_at = ?")
        values.append(_now())
    if status in ("completed", "failed"):
        fields.append("completed_at = ?")
        values.append(_now())
    if output is not None:
        fields.append("output = ?")
        values.append(output)
    if error is not None:
        fields.append("error = ?")
        values.append(error)
    values.extend([job_id, step_name])
    with get_db() as db:
        db.execute(f"UPDATE job_steps SET {', '.join(fields)} WHERE job_id = ? AND step_name = ?", values)


def get_cached_job(scenario_name: str, batch_date: str) -> dict | None:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM jobs WHERE scenario_name = ? AND batch_date = ? AND status = 'completed' ORDER BY completed_at DESC LIMIT 1",
            (scenario_name, batch_date),
        ).fetchone()
    return dict(row) if row else None
