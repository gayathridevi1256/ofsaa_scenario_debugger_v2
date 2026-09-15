"""Job-related Pydantic schemas."""

from pydantic import BaseModel


class StepOut(BaseModel):
    step_name: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    output: str | None = None
    error: str | None = None


class JobOut(BaseModel):
    job_id: str
    user_id: int
    log_filename: str
    log_file_path: str | None = None
    scenario_name: str | None = None
    batch_date: str | None = None
    status: str
    current_step: str | None = None
    started_at: str
    completed_at: str | None = None
    error_message: str | None = None
    output_dir: str | None = None
    alerts_generated: int = 0
    root_cause: str | None = None
    ai_recommendation: str | None = None
    result_json: str | None = None
    cte_results_json: str | None = None
    username: str | None = None
    steps: list[StepOut] = []


class RunResponse(BaseModel):
    job_id: str
    status: str
    cached: bool = False
    scenario_name: str | None = None
    batch_date: str | None = None
    cached_job_id: str | None = None
    cached_at: str | None = None
