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
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, TimestampMixin

VERSION_STATES = ("quarantined", "released", "rejected")
# Where reading the text of a version stands. "not_started" is for versions filed before F04.
TEXT_STATES = ("not_started", "pending", "running", "done", "failed", "unsupported")


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
        CheckConstraint(
            "text_state IN ('not_started', 'pending', 'running', 'done', 'failed', 'unsupported')",
            name="text_state",
        ),
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
    text_state: Mapped[str] = mapped_column(String(20), default="not_started")
    text_error: Mapped[str | None] = mapped_column(Text)
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


class Page(IdMixin, TimestampMixin, Base):
    """One page of a version. Pixel sizes are those of the image the text was read from."""

    __tablename__ = "page"
    __table_args__ = (UniqueConstraint("version_id", "number", name="uq_page_number"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_version.id"))
    number: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    has_native_text: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blank: Mapped[bool] = mapped_column(Boolean, default=False)


class OcrResult(IdMixin, TimestampMixin, Base):
    """The text of a page as one engine read it in one set of languages.

    Engine "native" means the text came from the file itself, with no recognition. Reading
    again in other languages adds a row; the newest row is the current text.
    """

    __tablename__ = "ocr_result"
    __table_args__ = (
        UniqueConstraint("page_id", "engine", "language", name="uq_ocr_result_run"),
        Index("ix_ocr_result_page", "tenant_id", "page_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("page.id"))
    engine: Mapped[str] = mapped_column(String(40))
    engine_version: Mapped[str] = mapped_column(String(60), default="")
    language: Mapped[str] = mapped_column(String(120), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    # Words with their boxes in the page's pixels: {"t": text, "x", "y", "w", "h", "c": 0..1}.
    boxes: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)
    confidence: Mapped[float | None] = mapped_column(Float)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class DocumentRelationship(IdMixin, TimestampMixin, Base):
    """A typed link from one document to another, such as an invoice to its purchase order.

    The link is stored once, in the direction it was made. Reading a document shows both the
    links it makes and the links made to it, each worded for the side being read.
    """

    __tablename__ = "document_relationship"
    __table_args__ = (
        UniqueConstraint(
            "from_document_id", "to_document_id", "relation_type", name="uq_document_relationship"
        ),
        CheckConstraint("from_document_id <> to_document_id", name="not_self"),
        CheckConstraint(
            "relation_type IN ('related', 'supersedes', 'responds_to', 'attached_to')",
            name="relation_type",
        ),
        Index("ix_document_relationship_to", "tenant_id", "to_document_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    from_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE")
    )
    to_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    relation_type: Mapped[str] = mapped_column(String(30))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
