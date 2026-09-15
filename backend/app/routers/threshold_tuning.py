"""Threshold tuning routes — standalone workflow, independent of a
diagnostic job, to inspect (and eventually tune) a scenario's configured
KDD_TSHLD threshold values from an uploaded log file."""

import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.models.threshold_tuning import (
    ThresholdAnalysisOut,
    ThresholdRecommendationsOut,
    ThresholdTuningReportRequest,
)
from app.services.auth_service import get_current_analyst
from app.services.threshold_tuning_service import get_current_thresholds, get_threshold_recommendations
from app.services.threshold_tuning_pdf_service import generate_threshold_tuning_report

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/threshold-tuning", tags=["threshold-tuning"])


@router.post("/analyze", response_model=ThresholdAnalysisOut)
def analyze_thresholds(file_path: str, user: dict = Depends(get_current_analyst)):
    try:
        return get_current_thresholds(file_path)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Threshold tuning analysis failed for %s", file_path)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")


@router.post("/recommend", response_model=ThresholdRecommendationsOut)
def recommend_thresholds(
    file_path: str,
    target_reduction_pct: float | None = None,
    user: dict = Depends(get_current_analyst),
):
    try:
        return get_threshold_recommendations(file_path, target_reduction_pct)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Threshold tuning recommendation failed for %s", file_path)
        raise HTTPException(status_code=500, detail=f"Recommendation failed: {e}")


@router.post("/report")
def download_threshold_tuning_report(body: ThresholdTuningReportRequest, user: dict = Depends(get_current_analyst)):
    """Formats the already-fetched analyze/recommend results into a PDF —
    does no further Oracle/SSH work, so this is fast regardless of how long
    the original analyze/recommend calls took."""
    try:
        pdf_bytes, filename = generate_threshold_tuning_report(
            body.analysis.model_dump(), body.recommendation.model_dump()
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        logger.exception("Threshold tuning report generation failed")
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")
