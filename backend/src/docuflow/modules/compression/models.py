"""Smaller copies of a version's file, and the measurements that justify them (F17)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, TimestampMixin


class Derivative(IdMixin, TimestampMixin, Base):
    """One attempt to make a smaller copy of one version.

    The original is never changed. A copy that failed the quality check is kept with its numbers
    but can never be recommended or downloaded.
    """

    __tablename__ = "derivative"
    __table_args__ = (
        CheckConstraint("status IN ('accepted', 'rejected', 'no_benefit')", name="status"),
        CheckConstraint("kind IN ('compressed')", name="kind"),
        Index("ix_derivative_version", "tenant_id", "version_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_version.id"))
    kind: Mapped[str] = mapped_column(String(20), default="compressed")
    status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)
    # Null when no copy was kept, for example a file that cannot be compressed.
    binary_object_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("binary_object.id"))
    mime: Mapped[str | None] = mapped_column(String(120))
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_size: Mapped[int] = mapped_column(BigInteger)
    derivative_size: Mapped[int] = mapped_column(BigInteger, default=0)
    producer: Mapped[str] = mapped_column(String(40), default="docuflow-compress")
    producer_version: Mapped[int] = mapped_column(Integer, default=1)
    # Everything needed to repeat the run: the profile and the versions of the libraries.
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
