"""Batch run schemas."""

from pydantic import BaseModel


class BatchRunRequest(BaseModel):
    file_paths: list[str]


class BatchJobItem(BaseModel):
    job_id: str
    file_path: str
    status: str


class BatchRunResponse(BaseModel):
    batch_id: str
    total: int
    jobs: list[BatchJobItem]
