"""Comments, mentions, tasks and in-app notifications (F10)."""

from __future__ import annotations

import uuid
from datetime import datetime

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
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, TimestampMixin


class Comment(IdMixin, TimestampMixin, Base):
    __tablename__ = "comment"
    __table_args__ = (Index("ix_comment_document", "tenant_id", "document_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    body: Mapped[str] = mapped_column(Text)


class Mention(IdMixin, Base):
    __tablename__ = "mention"
    __table_args__ = (UniqueConstraint("comment_id", "user_id", name="uq_mention"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    comment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comment.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))


class Task(IdMixin, TimestampMixin, Base):
    __tablename__ = "task"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'done', 'cancelled')", name="status"),
        CheckConstraint("priority IN ('low', 'normal', 'high')", name="priority"),
        Index("ix_task_document", "tenant_id", "document_id"),
        Index("ix_task_assignee", "tenant_id", "assignee_id", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    assignee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class Notification(IdMixin, TimestampMixin, Base):
    """A message for one person, shown in the app. Email and delivery tracking are F13."""

    __tablename__ = "notification"
    __table_args__ = (
        CheckConstraint("kind IN ('task_assigned', 'task_done', 'mention')", name="kind"),
        Index("ix_notification_user", "tenant_id", "user_id", "read_at", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE")
    )
    ref_id: Mapped[uuid.UUID | None] = mapped_column()
    title: Mapped[str] = mapped_column(String(300))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
