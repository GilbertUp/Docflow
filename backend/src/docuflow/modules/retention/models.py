"""Retention classes, legal holds and the disposal record (F15)."""

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


class RetentionClass(IdMixin, TimestampMixin, Base):
    """A schedule: keep documents for this many days after the trigger date."""

    __tablename__ = "retention_class"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_retention_class_name"),
        CheckConstraint("status IN ('active', 'archived')", name="status"),
        CheckConstraint("trigger IN ('created', 'last_version')", name="trigger"),
        CheckConstraint("duration_days >= 1", name="duration"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    duration_days: Mapped[int] = mapped_column(Integer)
    # When the clock starts: when the document was filed, or when its newest version was added.
    trigger: Mapped[str] = mapped_column(String(20), default="created")
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class DocumentRetention(IdMixin, TimestampMixin, Base):
    """The class a document is kept under."""

    __tablename__ = "document_retention"
    __table_args__ = (UniqueConstraint("document_id", name="uq_document_retention"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    class_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("retention_class.id"))
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class LegalHold(IdMixin, TimestampMixin, Base):
    """While a hold is active, the documents on it cannot be disposed of."""

    __tablename__ = "legal_hold"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_legal_hold_name"),
        CheckConstraint("status IN ('active', 'released')", name="status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    released_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(Text)


class LegalHoldItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "legal_hold_item"
    __table_args__ = (
        UniqueConstraint("hold_id", "document_id", name="uq_legal_hold_item"),
        Index("ix_legal_hold_item_document", "tenant_id", "document_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    hold_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_hold.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class DisposalEvent(IdMixin, TimestampMixin, Base):
    """The permanent record that a document was destroyed. It points at nothing that is deleted."""

    __tablename__ = "disposal_event"
    __table_args__ = (Index("ix_disposal_event_tenant", "tenant_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    # The document's id and title as they were. There is no foreign key: the row is gone.
    document_id: Mapped[uuid.UUID] = mapped_column()
    title: Mapped[str] = mapped_column(String(300))
    retention_class: Mapped[str] = mapped_column(String(120))
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # SHA-256 of every version's file, so it can be shown later what was destroyed.
    version_hashes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    approver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    reason: Mapped[str | None] = mapped_column(Text)
