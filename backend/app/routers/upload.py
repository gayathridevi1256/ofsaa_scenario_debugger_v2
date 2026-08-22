"""Upload route."""

import logging
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException

from app.models.upload import UploadResponse
from app.services.auth_service import get_current_analyst
from app.config import UPLOADS_DIR, MAX_UPLOAD_MB

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["upload"])


@router.post("/upload", response_model=UploadResponse)
async def upload_log(file: UploadFile = File(...), user: dict = Depends(get_current_analyst)):
    if not file.filename.lower().endswith((".log", ".txt")):
        raise HTTPException(status_code=400, detail="Only .log and .txt files allowed")

    raw_name = file.filename
    safe_name = f"{uuid.uuid4().hex}_{raw_name}"
    dest = UPLOADS_DIR / safe_name
    logger.info("Upload started: %s (%s) by %s", raw_name, safe_name, user["username"])

    try:
        content = await file.read()
        logger.info("Upload read complete: %d bytes", len(content))

        if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"File too large (max {MAX_UPLOAD_MB}MB)")

        dest.write_bytes(content)
        logger.info("Upload saved: %s", dest)

        return UploadResponse(filename=raw_name, saved_as=safe_name, file_path=str(dest), size_bytes=len(content))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Upload failed for %s", raw_name)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
