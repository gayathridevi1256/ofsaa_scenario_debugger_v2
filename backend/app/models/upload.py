"""Upload schema."""

from pydantic import BaseModel


class UploadResponse(BaseModel):
    filename: str
    saved_as: str
    file_path: str
    size_bytes: int
