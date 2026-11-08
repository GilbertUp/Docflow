"""Workflows: published step lists, running instances, and what happened (F11)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, TimestampMixin


class Workflow(IdMixin, TimestampMixin, Base):
    __tablename__ = "workflow"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_workflow_name"),
        CheckConstraint("status IN ('active', 'archived')", name="status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class WorkflowDefinition(IdMixin, TimestampMixin, Base):
    """One published version. Never changed: running instances keep the version they began on."""

    __tablename__ = "workflow_definition"
    __table_args__ = (UniqueConstraint("workflow_id", "version", name="uq_workflow_version"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow.id"))
    version: Mapped[int] = mapped_column(Integer)
    # [{"name", "kind": "approval" | "task", "assignee_id", "due_days"}], in order.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # {} for manual start, or {"event": "document.typed", "document_type_id": "..."}.
    trigger: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class WorkflowInstance(IdMixin, TimestampMixin, Base):
    __tablename__ = "workflow_instance"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'rejected', 'cancelled')", name="status"
        ),
        Index("ix_workflow_instance_document", "tenant_id", "document_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    definition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_definition.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class WorkflowStep(IdMixin, TimestampMixin, Base):
    __tablename__ = "workflow_step"
    __table_args__ = (
        UniqueConstraint("instance_id", "position", name="uq_workflow_step_position"),
        CheckConstraint(
            "status IN ('pending', 'active', 'approved', 'rejected', 'done', 'skipped')",
            name="status",
        ),
        CheckConstraint("kind IN ('approval', 'task')", name="kind"),
        Index("ix_workflow_step_task", "tenant_id", "task_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_instance.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))
    assignee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    # Who it was meant for, when a delegation sent it to someone else.
    delegated_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    due_days: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task.id"))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)


class WorkflowEvent(IdMixin, TimestampMixin, Base):
    """Every transition, in order. The history people read to see why a document is where it is."""

    __tablename__ = "workflow_event"
    __table_args__ = (
        Index("ix_workflow_event_instance", "tenant_id", "instance_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_instance.id", ondelete="CASCADE")
    )
    position: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Delegation(IdMixin, TimestampMixin, Base):
    """While it applies, new steps for one person go to another."""

    __tablename__ = "delegation"
    __table_args__ = (
        CheckConstraint("from_user_id <> to_user_id", name="not_self"),
        CheckConstraint("starts_at < ends_at", name="order"),
        Index("ix_delegation_from", "tenant_id", "from_user_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    from_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    to_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
