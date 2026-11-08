"""Request and response shapes for the forms API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChoiceModel(BaseModel):
    value: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)


class FormFieldModel(BaseModel):
    key: str = Field(max_length=40)
    label: str = Field(max_length=120)
    data_type: Literal["text", "number", "date", "boolean", "choice"]
    required: bool = False
    default_value: str | None = Field(default=None, max_length=500)
    validation: dict[str, Any] = Field(default_factory=dict)
    choices: list[ChoiceModel] = Field(default_factory=list)


class FormOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    status: str
    row_version: int
    version: int
    fields: list[FormFieldModel]


class CreateFormRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    fields: list[FormFieldModel]


class NewFormVersionRequest(BaseModel):
    row_version: int
    fields: list[FormFieldModel]


class UpdateFormRequest(BaseModel):
    row_version: int
    name: str | None = Field(default=None, max_length=120)
    status: Literal["active", "archived"] | None = None


class SubmitRequest(BaseModel):
    # Each answer as text, or a number or yes/no. A field left out counts as empty.
    answers: dict[str, Any] = Field(default_factory=dict)
    document_id: uuid.UUID | None = None


class SubmissionOut(BaseModel):
    id: uuid.UUID
    form_name: str
    form_version: int
    submitter_id: uuid.UUID
    submitter_name: str
    document_id: uuid.UUID | None
    created_at: datetime
    labels: dict[str, str]
    data: dict[str, Any]
