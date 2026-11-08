"""Request and response shapes for the workflow API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class StepModel(BaseModel):
    name: str = Field(max_length=120)
    kind: Literal["approval", "task"]
    assignee_id: uuid.UUID
    due_days: int | None = Field(default=None, ge=0, le=365)


class WorkflowOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    status: str
    row_version: int
    version: int
    steps: list[StepModel]
    trigger: dict[str, Any]


class CreateWorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    steps: list[StepModel]
    trigger: dict[str, Any] = Field(default_factory=dict)


class NewWorkflowVersionRequest(BaseModel):
    row_version: int
    steps: list[StepModel]
    trigger: dict[str, Any] = Field(default_factory=dict)


class UpdateWorkflowRequest(BaseModel):
    row_version: int
    name: str | None = Field(default=None, max_length=120)
    status: Literal["active", "archived"] | None = None


class StartWorkflowRequest(BaseModel):
    workflow_id: uuid.UUID


class StepOut(BaseModel):
    id: uuid.UUID
    position: int
    name: str
    kind: str
    status: str
    assignee_id: uuid.UUID
    assignee_name: str
    delegated_from: str | None
    decided_at: datetime | None
    comment: str | None
    due_days: int | None


class EventOut(BaseModel):
    kind: str
    position: int | None
    actor_id: uuid.UUID | None
    details: dict[str, Any]
    created_at: datetime


class InstanceOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    workflow_name: str
    version: int
    status: str
    started_by: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None
    can_cancel: bool
    steps: list[StepOut]
    events: list[EventOut]


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "complete"]
    comment: str = Field(default="", max_length=2000)


class DelegationRequest(BaseModel):
    to_user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime


class DelegationOut(BaseModel):
    id: uuid.UUID
    to_user_id: uuid.UUID
    to_name: str
    starts_at: datetime
    ends_at: datetime


class PersonOut(BaseModel):
    id: uuid.UUID
    name: str
