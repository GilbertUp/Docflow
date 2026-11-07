"""Stored bytes and immutable document versions. A document keeps one identity while versions and
binaries come and go; nothing here is ever updated in place except a reference count."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, TimestampMixin

VERSION_STATES = ("quarantined", "released", "rejected")


class BinaryObject(IdMixin, TimestampMixin, Base):
    """One stored, encrypted object. Deduplicated per organization by content hash."""

    __tablename__ = "binary_object"
    __table_args__ = (UniqueConstraint("tenant_id", "sha256", name="uq_binary_object_content"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str] = mapped_column(String(120))
    object_key: Mapped[str] = mapped_column(String(300))
    wrapped_data_key: Mapped[bytes] = mapped_column(LargeBinary)
    ref_count: Mapped[int] = mapped_column(Integer, default=1)


class DocumentVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "document_version"
    __table_args__ = (
        UniqueConstraint("document_id", "number", name="uq_document_version_number"),
        Index("ix_document_version_binary", "tenant_id", "binary_object_id"),
        CheckConstraint("state IN ('quarantined', 'released', 'rejected')", name="state"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"))
    number: Mapped[int] = mapped_column(Integer)
    binary_object_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("binary_object.id"))
    # The name the person uploaded. Metadata only: it never reaches a storage key.
    original_filename: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(Text, default="")
    # Which scanner cleared the file. "none" means it was not scanned.
    scan_engine: Mapped[str] = mapped_column(String(20), default="none")
    state: Mapped[str] = mapped_column(String(20), default="released")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class IngestionJob(IdMixin, TimestampMixin, Base):
    """One ZIP import. The archive is staged encrypted and removed when the import ends."""

    __tablename__ = "ingestion_job"
    __table_args__ = (
        Index("ix_ingestion_job_submitter", "tenant_id", "submitted_by", "created_at"),
        CheckConstraint("status IN ('queued', 'running', 'completed', 'failed')", name="status"),
        CheckConstraint("source IN ('archive')", name="source"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    source: Mapped[str] = mapped_column(String(20), default="archive")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    submitted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    cabinet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cabinet.id"))
    folder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folder.id"))
    archive_name: Mapped[str] = mapped_column(String(300))
    archive_size: Mapped[int] = mapped_column(BigInteger)
    # The staged copy. Both are cleared once the import has ended.
    archive_key: Mapped[str | None] = mapped_column(String(300))
    archive_wrapped_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionItem(IdMixin, TimestampMixin, Base):
    """What happened to one file in an import. Also what makes a retry skip finished work."""

    __tablename__ = "ingestion_item"
    __table_args__ = (
        UniqueConstraint("job_id", "path", name="uq_ingestion_item_path"),
        CheckConstraint("status IN ('filed', 'failed')", name="status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_job.id"))
    path: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text, default="")
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"))
    duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
