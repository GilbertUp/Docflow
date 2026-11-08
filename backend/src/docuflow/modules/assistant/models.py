"""Assistant settings and the record of what was asked (F18)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, TimestampMixin


class AssistantSetting(IdMixin, TimestampMixin, Base):
    """What the assistant may do for one organization. Generated answers start switched off."""

    __tablename__ = "assistant_setting"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_assistant_setting_tenant"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    generation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Cabinets whose text is never sent to an AI provider. It can still be shown as extracts.
    excluded_cabinet_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class AssistantQuery(IdMixin, TimestampMixin, Base):
    """One question and how it was answered. The prompt sent to a provider is never kept."""

    __tablename__ = "assistant_query"
    __table_args__ = (
        CheckConstraint("mode IN ('generated', 'extracts', 'insufficient_evidence')", name="mode"),
        Index("ix_assistant_query_user", "tenant_id", "user_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    question: Mapped[str] = mapped_column(String(500))
    mode: Mapped[str] = mapped_column(String(30))
    answer: Mapped[str | None] = mapped_column(Text)
    # [{"n", "document_id", "page", "chunk_id"}]: where each numbered source came from.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    best_coverage: Mapped[float] = mapped_column(Float, default=0.0)
