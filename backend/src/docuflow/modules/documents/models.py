"""Stored bytes and immutable document versions. A document keeps one identity while versions and
binaries come and go; nothing here is ever updated in place except a reference count."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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
    state: Mapped[str] = mapped_column(String(20), default="released")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
