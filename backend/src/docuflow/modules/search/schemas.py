"""Response shapes for the search API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class HitOut(BaseModel):
    id: uuid.UUID
    title: str
    cabinet_id: uuid.UUID
    folder_id: uuid.UUID | None
    document_type_id: uuid.UUID | None
    type_name: str | None
    size_bytes: int
    created_at: datetime
    matched_in: str
    score: float
    # A few words around the match, with matches marked [[like this]]. Only for text the caller
    # may see.
    snippet: str | None


class SearchOut(BaseModel):
    query: str
    total: int
    truncated: bool
    hits: list[HitOut]
