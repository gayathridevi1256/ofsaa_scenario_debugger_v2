"""Threshold tuning schemas."""

from pydantic import BaseModel


class ThresholdOut(BaseModel):
    name: str
    display_name: str
    current_value: str | None = None
    min_value: str | None = None
    max_value: str | None = None
    unit: str | None = None
    description: str | None = None


class ThresholdAnalysisOut(BaseModel):
    scenario_name: str
    tshld_set_id: str
    alert_count: int
    thresholds: list[ThresholdOut]


class ThresholdRecommendationOut(BaseModel):
    recommended_value: float | None = None
    current_value: float | None = None
    note: str


class AppliedChangeOut(BaseModel):
    name: str
    display_name: str
    current_value: float
    recommended_value: float


class SkippedChangeOut(BaseModel):
    name: str
    reason: str


class ThresholdRecommendationsOut(BaseModel):
    recommendations: dict[str, ThresholdRecommendationOut]
    current_alert_count: int
    projected_alert_count: int | None = None
    alert_count_delta: int | None = None
    target_reduction_pct: float | None = None
    target_met: bool | None = None
    applied_changes: list[AppliedChangeOut] = []
    skipped_changes: list[SkippedChangeOut] = []


class ThresholdTuningReportRequest(BaseModel):
    """PDF report request — takes the exact JSON already returned by
    /analyze and /recommend (the frontend already has both in state after
    a tune) rather than a file_path, so generating the report never re-runs
    the SSH batch-date step or re-executes the dataset query: every number
    in the report was already live-verified when those endpoints ran."""
    analysis: ThresholdAnalysisOut
    recommendation: ThresholdRecommendationsOut
