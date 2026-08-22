"""Job service — run pipeline, rerun, batch, list, get."""

import uuid
import asyncio
import logging
from app.database.job_repo import create_job, get_job, list_jobs, update_job_status
from app.database.audit_repo import audit
from app.pipeline.orchestrator import run_pipeline
from app.config import PIPELINE_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

BATCH_MAX_FILES = 20
BATCH_POLL_INTERVAL = 2  # seconds between status checks


async def _forward_queue_to_ws(queue: asyncio.Queue, manager, job_id: str):
    try:
        while True:
            msg = await queue.get()
            if msg.get("event") in ("job_completed", "job_failed"):
                await manager.send(job_id, msg)
                break
            await manager.send(job_id, msg)
    finally:
        manager.clear_buffer(job_id)


async def run_pipeline_job(file_path: str, user: dict, force: bool = False, manager=None) -> dict:
    filename = file_path.split("/")[-1].split("\\")[-1]

    job_id = str(uuid.uuid4())
    job = create_job(job_id, user["id"], filename)
    audit("pipeline_started", username=user["username"], user_id=user["id"], job_id=job_id, detail=f"File: {filename}")

    queue = asyncio.Queue()
    asyncio.create_task(_forward_queue_to_ws(queue, manager, job_id))
    asyncio.create_task(run_pipeline(job_id, user["id"], user["username"], file_path, queue))
    return {"job_id": job_id, "status": "pending", "cached": False}


async def _wait_for_job(job_id: str) -> str:
    """Poll job status until completed or failed. Returns final status."""
    elapsed = 0
    while elapsed < PIPELINE_TIMEOUT_SECONDS:
        job = get_job(job_id)
        if not job:
            return "not_found"
        status = job.get("status", "unknown")
        if status in ("completed", "failed"):
            return status
        await asyncio.sleep(BATCH_POLL_INTERVAL)
        elapsed += BATCH_POLL_INTERVAL
    return "timeout"


async def run_batch_jobs(file_paths: list[str], user: dict, manager=None) -> dict:
    if len(file_paths) > BATCH_MAX_FILES:
        file_paths = file_paths[:BATCH_MAX_FILES]

    batch_id = str(uuid.uuid4())
    results = []

    for idx, fp in enumerate(file_paths, 1):
        logger.info("Batch [%s] starting job %d/%d: %s", batch_id, idx, len(file_paths), fp)
        result = await run_pipeline_job(fp, user, force=True, manager=manager)
        job_id = result.get("job_id", "")

        final_status = await _wait_for_job(job_id)
        logger.info("Batch [%s] job %d/%d finished: %s (%s)", batch_id, idx, len(file_paths), job_id, final_status)

        results.append({
            "job_id": job_id,
            "file_path": fp,
            "status": final_status,
        })

    return {"batch_id": batch_id, "total": len(results), "jobs": results}


async def rerun_job(job_id: str, user: dict, force: bool = False, manager=None) -> dict:
    job = get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
    file_path = str(__import__('app.config', fromlist=['UPLOADS_DIR']).UPLOADS_DIR / job["log_filename"])
    return await run_pipeline_job(file_path, user, force=force, manager=manager)


def get_job_detail(job_id: str) -> dict | None:
    return get_job(job_id)


def get_job_list(user: dict, limit: int = 50) -> list[dict]:
    uid = None if user["role"] == "admin" else user["id"]
    return list_jobs(user_id=uid, limit=limit)
