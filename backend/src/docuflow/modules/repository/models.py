"""Filing structure. Every cabinet and folder owns an ACL anchor, and the anchor tree mirrors the
folder tree, so moving a folder re-parents one anchor and every document below follows."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, TimestampMixin

STATUSES = ("active", "archived")


class Cabinet(IdMixin, TimestampMixin, Base):
    __tablename__ = "cabinet"
    __table_args__ = (
        Index("uq_cabinet_name", "tenant_id", text("lower(name)"), unique=True),
        CheckConstraint("status IN ('active', 'archived')", name="status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    acl_anchor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acl_anchor.id"), unique=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class Folder(IdMixin, TimestampMixin, Base):
    __tablename__ = "folder"
    __table_args__ = (
        Index(
            "uq_folder_root_name",
            "tenant_id",
            "cabinet_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("parent_id IS NULL"),
        ),
        Index(
            "uq_folder_child_name",
            "tenant_id",
            "parent_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("parent_id IS NOT NULL"),
        ),
        Index("ix_folder_parent", "tenant_id", "parent_id"),
        Index("ix_folder_cabinet", "tenant_id", "cabinet_id"),
        CheckConstraint("status IN ('active', 'archived')", name="status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    cabinet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cabinet.id"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folder.id"))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")
    depth: Mapped[int] = mapped_column(Integer, default=1)
    acl_anchor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acl_anchor.id"), unique=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class Document(IdMixin, TimestampMixin, Base):
    """The filing record. F03 adds versions, binaries and processing around this identity."""

    __tablename__ = "document"
    __table_args__ = (
        Index("ix_document_folder", "tenant_id", "folder_id"),
        Index("ix_document_cabinet", "tenant_id", "cabinet_id"),
        Index("ix_document_anchor", "tenant_id", "acl_anchor_id"),
        CheckConstraint("status IN ('active', 'archived')", name="status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    cabinet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cabinet.id"))
    folder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folder.id"))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="active")
    # The nearest anchor: the folder's own, or the document's when it has exceptions.
    acl_anchor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acl_anchor.id"))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    # The latest released version. The foreign key is added after both tables exist, because
    # a version also points back at its document.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "document_version.id",
            use_alter=True,
            name="fk_document_current_version_id_document_version",
        )
    )
    # What kind of document this is, and the field set its values were written against (F06).
    document_type_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_type.id"))
    schema_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("index_schema_version.id")
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
