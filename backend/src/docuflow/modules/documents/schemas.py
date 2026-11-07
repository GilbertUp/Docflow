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
    text_state: str
    size_bytes: int
    mime: str
    created_at: datetime


class ImportItemOut(BaseModel):
    path: str
    status: str
    message: str
    document_id: uuid.UUID | None
    duplicate: bool


class ImportOut(BaseModel):
    id: uuid.UUID
    status: str
    archive_name: str
    file_count: int
    filed: int
    duplicates: int
    failed: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    # Failed files come first, then filed ones, up to a limit.
    items: list[ImportItemOut]


class PageTextOut(BaseModel):
    number: int
    source: str
    language: str
    confidence: float | None
    is_blank: bool
    text: str


class TextOut(BaseModel):
    version: int
    state: str
    error: str | None
    total_pages: int
    pages: list[PageTextOut]


class TextRequestOut(BaseModel):
    version: int
    state: str
    languages: list[str]


class LanguagesOut(BaseModel):
    engine: str
    engine_version: str
    available: bool
    languages: list[str]
    defaults: list[str]
    max_per_run: int


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
