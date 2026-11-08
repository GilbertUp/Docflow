"""Request and response shapes for the indexing API."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

DataType = Literal["text", "number", "date", "boolean", "choice"]


class ChoiceModel(BaseModel):
    value: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)


class FieldModel(BaseModel):
    key: str = Field(max_length=40)
    label: str = Field(max_length=120)
    data_type: DataType
    required: bool = False
    read_only: bool = False
    default_value: str | None = Field(default=None, max_length=500)
    validation: dict[str, Any] = Field(default_factory=dict)
    choices: list[ChoiceModel] = Field(default_factory=list)
    # Where to find the value in the text: {"labels": [...], "pattern": "..."} (F07).
    extraction: dict[str, Any] = Field(default_factory=dict)


class TypeOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    status: str
    row_version: int
    version: int | None
    fields: list[FieldModel]
    keywords: list[str] = Field(default_factory=list)
    auto_accept_threshold: float = 0.9
    review_threshold: float = 0.6


class CreateTypeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    fields: list[FieldModel]
    keywords: list[str] = Field(default_factory=list, max_length=20)
    auto_accept_threshold: float = Field(default=0.9, gt=0, le=1)
    review_threshold: float = Field(default=0.6, gt=0, le=1)


class NewVersionRequest(BaseModel):
    row_version: int
    fields: list[FieldModel]


class UpdateTypeRequest(BaseModel):
    row_version: int
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    status: Literal["active", "archived"] | None = None
    keywords: list[str] | None = Field(default=None, max_length=20)
    auto_accept_threshold: float | None = Field(default=None, gt=0, le=1)
    review_threshold: float | None = Field(default=None, gt=0, le=1)


class IndexFieldOut(FieldModel):
    value: Any = None


class IndexOut(BaseModel):
    document_id: uuid.UUID
    document_type_id: uuid.UUID | None
    type_name: str | None
    schema_version: int | None
    current_version: int | None
    outdated: bool
    complete: bool
    missing_required: list[str]
    row_version: int
    fields: list[IndexFieldOut]


class SetIndexRequest(BaseModel):
    document_type_id: uuid.UUID | None
    # Every editable field to keep. A field left out is cleared. Numbers and dates may be text.
    values: dict[str, Any] = Field(default_factory=dict)
    row_version: int | None = None


class ClassificationOut(BaseModel):
    type_id: uuid.UUID | None
    type_name: str | None
    confidence: float
    band: str
    applied: bool


class SuggestedFieldOut(BaseModel):
    key: str
    label: str
    data_type: str
    raw_value: str
    value: str | None
    confidence: float
    band: str
    method: str
    page_number: int | None
    bbox: dict[str, Any]
    validation_state: str
    state: str


class SuggestionsOut(BaseModel):
    version_number: int
    text_state: str
    classification: ClassificationOut | None
    fields: list[SuggestedFieldOut]


class RunOut(BaseModel):
    version_number: int
