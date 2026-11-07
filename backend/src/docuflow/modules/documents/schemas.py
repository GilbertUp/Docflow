"""Request and response shapes for the documents API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from docuflow.modules.repository.schemas import DocumentOut


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: int
    original_filename: str
    state: str
    reason: str
    scan_engine: str
    size_bytes: int
    mime: str
    created_at: datetime


class VersionResult(BaseModel):
    document: DocumentOut
    version: VersionOut
    duplicate: bool


class UploadResult(BaseModel):
    document: DocumentOut
    version: VersionOut
    duplicate: bool
    # Set only when the caller may read the earlier document with the same content.
    duplicate_of: uuid.UUID | None
