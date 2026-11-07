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


class TypeOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    status: str
    row_version: int
    version: int | None
    fields: list[FieldModel]


class CreateTypeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    fields: list[FieldModel]


class NewVersionRequest(BaseModel):
    row_version: int
    fields: list[FieldModel]


class UpdateTypeRequest(BaseModel):
    row_version: int
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    status: Literal["active", "archived"] | None = None


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
