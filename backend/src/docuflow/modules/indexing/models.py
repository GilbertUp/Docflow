"""Document types and the fields that describe a document of each type (F06).

A type's fields live in immutable schema versions, so changing a type never rewrites what was
already recorded: each document points at the version its values were written against.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, TimestampMixin, utcnow

DATA_TYPES = ("text", "number", "date", "boolean", "choice")


class DocumentType(IdMixin, TimestampMixin, Base):
    __tablename__ = "document_type"
    __table_args__ = (
        Index("uq_document_type_name", "tenant_id", text("lower(name)"), unique=True),
        CheckConstraint("status IN ('active', 'archived')", name="status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    # The schema version given to documents typed from now on. Set when a version is published.
    current_schema_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "index_schema_version.id",
            use_alter=True,
            name="fk_document_type_current_schema",
        )
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class IndexSchemaVersion(IdMixin, TimestampMixin, Base):
    """An immutable set of fields. A new one is made for every change to a type's fields."""

    __tablename__ = "index_schema_version"
    __table_args__ = (UniqueConstraint("document_type_id", "version", name="uq_schema_version"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    document_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_type.id"))
    version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class IndexField(IdMixin, TimestampMixin, Base):
    __tablename__ = "index_field"
    __table_args__ = (
        UniqueConstraint("schema_version_id", "key", name="uq_index_field_key"),
        CheckConstraint(
            "data_type IN ('text', 'number', 'date', 'boolean', 'choice')", name="data_type"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    schema_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("index_schema_version.id"))
    key: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(120))
    data_type: Mapped[str] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(Integer, default=0)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    # Read-only fields are filled by the system, for example from extraction, never typed.
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # pattern, min_length, max_length for text; min, max for numbers and dates.
    validation: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    default_value: Mapped[str | None] = mapped_column(String(500))


class FieldChoice(IdMixin, TimestampMixin, Base):
    __tablename__ = "field_choice"
    __table_args__ = (UniqueConstraint("field_id", "value", name="uq_field_choice_value"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    field_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("index_field.id"))
    value: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)


class IndexValue(IdMixin, TimestampMixin, Base):
    """The recorded value of one field of one document, in the column that fits its type."""

    __tablename__ = "index_value"
    __table_args__ = (
        UniqueConstraint("document_id", "field_id", name="uq_index_value_field"),
        Index("ix_index_value_field_text", "tenant_id", "field_id", "value_text"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"))
    field_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("index_field.id"))
    value_text: Mapped[str | None] = mapped_column(Text)
    value_number: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    value_date: Mapped[date | None] = mapped_column(Date)
    value_bool: Mapped[bool | None] = mapped_column(Boolean)
    validated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
