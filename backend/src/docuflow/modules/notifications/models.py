"""Deadlines, who wants email, and the email that was sent (F13)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class SlaPolicy(IdMixin, TimestampMixin, Base):
    """When to remind about, and escalate, a task that has a due time. One per organization."""

    __tablename__ = "sla_policy"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_sla_policy_tenant"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    # Hours before the due time to remind the assignee. Zero means no reminder.
    reminder_before_hours: Mapped[int] = mapped_column(Integer, default=24)
    # Hours after the due time to tell someone else. Null means never.
    escalate_after_hours: Mapped[int | None] = mapped_column(Integer)
    # Who is told. Null means whoever made the task.
    escalate_to_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class NotificationPreference(IdMixin, TimestampMixin, Base):
    """Whether one person also wants one kind of notification by email."""

    __tablename__ = "notification_preference"
    __table_args__ = (UniqueConstraint("user_id", "kind", name="uq_notification_preference"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    email: Mapped[bool] = mapped_column(Boolean, default=False)


class EmailMessage(IdMixin, TimestampMixin, Base):
    """One email to one person, and what happened when it was sent."""

    __tablename__ = "email_message"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'sent', 'failed')", name="status"),
        Index("ix_email_message_user", "tenant_id", "user_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    notification_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("notification.id", ondelete="SET NULL")
    )
    to_address: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
