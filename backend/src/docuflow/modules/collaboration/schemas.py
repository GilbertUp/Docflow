"""Request and response shapes for the collaboration API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Priority = Literal["low", "normal", "high"]
Status = Literal["open", "done", "cancelled"]


class CommentOut(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID
    author_name: str
    body: str
    mentioned: list[str]
    created_at: datetime
    can_delete: bool


class AddCommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class PersonOut(BaseModel):
    id: uuid.UUID
    name: str


class TaskOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    title: str
    description: str
    status: str
    priority: str
    assignee_id: uuid.UUID
    assignee_name: str
    due_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    row_version: int
    can_change: bool


class CreateTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    assignee_id: uuid.UUID
    priority: Priority = "normal"
    due_at: datetime | None = None


class UpdateTaskRequest(BaseModel):
    row_version: int
    status: Status | None = None
    title: str | None = Field(default=None, max_length=200)
    priority: Priority | None = None
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None
    clear_due: bool = False


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    document_id: uuid.UUID | None
    created_at: datetime
    read: bool


class NotificationsOut(BaseModel):
    unread: int
    items: list[NotificationOut]
