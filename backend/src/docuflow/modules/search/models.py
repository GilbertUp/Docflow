"""The searchable form of a document (F08)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Computed, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, utcnow


class SearchEntry(IdMixin, Base):
    """One row per document, rebuilt whenever its title, details or text change.

    The details (``meta``: title, type and field values) and the page text (``body``) are kept
    apart, because seeing the text needs the preview right and seeing the details only needs read.
    """

    __tablename__ = "search_entry"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_search_entry_document"),
        Index("ix_search_entry_meta", "meta_tsv", postgresql_using="gin"),
        Index("ix_search_entry_body", "body_tsv", postgresql_using="gin"),
        Index("ix_search_entry_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    version_number: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    meta_tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', meta)", persisted=True)
    )
    body_tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', body)", persisted=True)
    )
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
