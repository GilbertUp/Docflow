"""Approvals, signatures and stamps, each tied to the exact file they were made on (F14)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, TimestampMixin


class Approval(IdMixin, TimestampMixin, Base):
    """A decision on a workflow step, bound to the version and the bytes it was made on."""

    __tablename__ = "approval"
    __table_args__ = (
        CheckConstraint("decision IN ('approve', 'reject')", name="decision"),
        Index("ix_approval_document", "tenant_id", "document_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_version.id"))
    # SHA-256 of the file the decider was shown, so the decision cannot be moved to other bytes.
    version_hash: Mapped[str] = mapped_column(String(64))
    step_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_step.id"))
    decider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    decision: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str | None] = mapped_column(Text)


class Signature(IdMixin, TimestampMixin, Base):
    """A person's signed statement about one version. ``kind`` says how strong it is."""

    __tablename__ = "signature"
    __table_args__ = (
        CheckConstraint("kind IN ('internal_attestation', 'external_esignature')", name="kind"),
        CheckConstraint("status IN ('valid', 'revoked')", name="status"),
        Index("ix_signature_document", "tenant_id", "document_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_version.id"))
    signer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    kind: Mapped[str] = mapped_column(String(30), default="internal_attestation")
    statement: Mapped[str] = mapped_column(String(500))
    bound_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="valid")
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval.id"))
    # Set only for an external provider, which is a later slice.
    provider: Mapped[str | None] = mapped_column(String(40))
    provider_reference: Mapped[str | None] = mapped_column(String(200))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)


class Stamp(IdMixin, TimestampMixin, Base):
    """A reusable mark such as APPROVED or PAID."""

    __tablename__ = "stamp"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_stamp_name"),
        CheckConstraint("status IN ('active', 'archived')", name="status"),
        CheckConstraint("color IN ('red', 'green', 'blue', 'black')", name="color"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(60))
    text: Mapped[str] = mapped_column(String(30))
    color: Mapped[str] = mapped_column(String(10), default="red")
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class StampApplication(IdMixin, TimestampMixin, Base):
    """A stamp placed on one page of one version. The text is kept as it was when applied."""

    __tablename__ = "stamp_application"
    __table_args__ = (Index("ix_stamp_application_version", "tenant_id", "version_id", "page"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_version.id"))
    stamp_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stamp.id"))
    page: Mapped[int] = mapped_column(Integer)
    # Position of the stamp's centre as a share of the page, from the top left.
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(String(30))
    color: Mapped[str] = mapped_column(String(10))
    applied_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
