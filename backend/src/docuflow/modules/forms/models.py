"""Forms people fill in, and what they submitted (F12)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, TimestampMixin


class Form(IdMixin, TimestampMixin, Base):
    __tablename__ = "form"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_form_name"),
        CheckConstraint("status IN ('active', 'archived')", name="status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class FormVersion(IdMixin, TimestampMixin, Base):
    """One published set of fields. Never changed, so old submissions keep their meaning."""

    __tablename__ = "form_version"
    __table_args__ = (UniqueConstraint("form_id", "version", name="uq_form_version"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    form_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("form.id"))
    version: Mapped[int] = mapped_column(Integer)
    # [{"key", "label", "data_type", "required", "default_value", "validation", "choices"}]
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class FormSubmission(IdMixin, TimestampMixin, Base):
    __tablename__ = "form_submission"
    __table_args__ = (
        Index("ix_form_submission_form", "tenant_id", "form_version_id", "created_at"),
        Index("ix_form_submission_submitter", "tenant_id", "submitter_id", "created_at"),
        Index("ix_form_submission_document", "tenant_id", "document_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    form_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("form_version.id"))
    submitter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE")
    )
    # Each answer as text in its field's form (numbers exact, dates ISO), or null when left empty.
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
