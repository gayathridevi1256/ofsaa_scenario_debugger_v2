"""Job routes — run, batch, rerun, list, get."""

from fastapi import APIRouter, Depends, Query, HTTPException, BackgroundTasks
from fastapi.responses import Response

from app.models.job import JobOut, RunResponse
from app.models.batch import BatchRunRequest, BatchRunResponse
from app.services.job_service import run_pipeline_job, run_batch_jobs, rerun_job, get_job_detail, get_job_list
from app.services.auth_service import get_current_analyst
from app.services.pdf_service import generate_job_report, generate_batch_report
from app.websocket.manager import manager

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("/run")
async def run_pipeline(
    file_path: str,
    force: bool = Query(False),
    user: dict = Depends(get_current_analyst),
):
    result = await run_pipeline_job(file_path, user, force=force, manager=manager)
    return RunResponse(job_id=result["job_id"], status=result["status"], cached=False)


@router.post("/batch", response_model=BatchRunResponse)
async def batch_run(req: BatchRunRequest, user: dict = Depends(get_current_analyst)):
    result = await run_batch_jobs(req.file_paths, user, manager=manager)
    return result


@router.post("/{job_id}/rerun")
async def rerun(job_id: str, force: bool = Query(False), user: dict = Depends(get_current_analyst)):
    try:
        result = await rerun_job(job_id, user, force=force, manager=manager)
        return RunResponse(job_id=result.get("job_id", ""), status=result.get("status", ""), cached=result.get("cached", False))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("", response_model=list[JobOut])
def list_all_jobs(limit: int = Query(50, le=200), user: dict = Depends(get_current_analyst)):
    jobs = get_job_list(user, limit=limit)
    return [JobOut(**j) for j in jobs]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, user: dict = Depends(get_current_analyst)):
    job = get_job_detail(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobOut(**job)


@router.post("/batch-report")
def download_batch_report(job_ids: str, user: dict = Depends(get_current_analyst)):
    ids = [jid.strip() for jid in job_ids.split(",") if jid.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="No job IDs provided")
    try:
        pdf_bytes = generate_batch_report(ids)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="batch_report_{ts}.pdf"'},
    )


@router.get("/{job_id}/report")
def download_job_report(job_id: str, user: dict = Depends(get_current_analyst)):
    job = get_job_detail(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        pdf_bytes = generate_job_report(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    filename = f"report_{job_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
