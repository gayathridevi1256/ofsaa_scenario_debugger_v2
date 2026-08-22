"""Health check routes."""

from fastapi import APIRouter
from app.config import APP_NAME, APP_VERSION
from app.database.job_repo import list_jobs

router = APIRouter()


@router.get("/")
def root():
    return {"name": APP_NAME, "version": APP_VERSION, "status": "running"}


@router.get("/api/health")
def health():
    jobs = list_jobs(limit=1)
    return {"status": "healthy", "database": "connected", "job_count": len(jobs) if jobs else 0}
