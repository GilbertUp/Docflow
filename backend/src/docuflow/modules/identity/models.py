"""Identity tables. All tenant-owned tables are protected by row-level security."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, TimestampMixin

PRINCIPAL_TYPES = ("user", "group", "role", "department")
RESOURCE_TYPES = ("tenant", "cabinet", "folder", "document")


class Department(IdMixin, TimestampMixin, Base):
    __tablename__ = "department"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("department.id"))


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email"),
        CheckConstraint("kind IN ('person', 'service')", name="kind"),
        CheckConstraint("status IN ('active', 'disabled')", name="status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="person")
    status: Mapped[str] = mapped_column(String(20), default="active")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("department.id"))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class Group(IdMixin, TimestampMixin, Base):
    __tablename__ = "user_group"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")


class GroupMember(IdMixin, TimestampMixin, Base):
    __tablename__ = "group_member"
    __table_args__ = (
        UniqueConstraint("tenant_id", "group_id", "user_id"),
        Index("ix_group_member_user", "tenant_id", "user_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_group.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))


class Permission(Base):
    """Global vocabulary, seeded by migration. Holds no tenant data, so it has no tenant_id."""

    __tablename__ = "permission"
    __table_args__ = (CheckConstraint("kind IN ('system', 'resource')", name="kind"),)

    code: Mapped[str] = mapped_column(String(60), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)


class Role(IdMixin, TimestampMixin, Base):
    __tablename__ = "role"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")


class RolePermission(IdMixin, Base):
    __tablename__ = "role_permission"
    __table_args__ = (UniqueConstraint("tenant_id", "role_id", "permission"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role.id", ondelete="CASCADE"))
    permission: Mapped[str] = mapped_column(ForeignKey("permission.code"))


class RoleAssignment(IdMixin, TimestampMixin, Base):
    __tablename__ = "role_assignment"
    __table_args__ = (
        CheckConstraint("(user_id IS NULL) <> (group_id IS NULL)", name="one_holder"),
        UniqueConstraint("tenant_id", "role_id", "user_id", name="uq_role_assignment_user"),
        UniqueConstraint("tenant_id", "role_id", "group_id", name="uq_role_assignment_group"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_group.id", ondelete="CASCADE")
    )


class AclAnchor(IdMixin, TimestampMixin, Base):
    """A node with explicit permissions. Everything else points at its nearest anchor."""

    __tablename__ = "acl_anchor"
    __table_args__ = (
        UniqueConstraint("tenant_id", "resource_type", "resource_id"),
        CheckConstraint(
            "resource_type IN ('tenant', 'cabinet', 'folder', 'document')", name="resource_type"
        ),
        Index("ix_acl_anchor_parent", "tenant_id", "parent_anchor_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    resource_type: Mapped[str] = mapped_column(String(20))
    resource_id: Mapped[uuid.UUID] = mapped_column()
    parent_anchor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acl_anchor.id"))


class AclEntry(IdMixin, TimestampMixin, Base):
    __tablename__ = "acl_entry"
    __table_args__ = (
        UniqueConstraint("anchor_id", "principal_type", "principal_id", "permission"),
        CheckConstraint(
            "principal_type IN ('user', 'group', 'role', 'department')", name="principal_type"
        ),
        CheckConstraint("effect IN ('allow', 'deny')", name="effect"),
        Index("ix_acl_entry_principal", "tenant_id", "principal_type", "principal_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    anchor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acl_anchor.id", ondelete="CASCADE"))
    principal_type: Mapped[str] = mapped_column(String(20))
    principal_id: Mapped[uuid.UUID] = mapped_column()
    permission: Mapped[str] = mapped_column(ForeignKey("permission.code"))
    effect: Mapped[str] = mapped_column(String(10))


class UserSession(IdMixin, TimestampMixin, Base):
    """Only hashes are stored, so a database leak does not reveal usable sessions."""

    __tablename__ = "user_session"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint("state IN ('active', 'mfa_pending')", name="state"),
        Index("ix_user_session_user", "tenant_id", "user_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="active")
    device: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MfaCredential(IdMixin, TimestampMixin, Base):
    __tablename__ = "mfa_credential"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    secret_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_step: Mapped[int] = mapped_column(BigInteger, default=0)


class MfaRecoveryCode(IdMixin, TimestampMixin, Base):
    __tablename__ = "mfa_recovery_code"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "code_hash"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OidcProvider(IdMixin, TimestampMixin, Base):
    __tablename__ = "oidc_provider"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(100))
    issuer: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(255))
    client_secret_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    group_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class OidcIdentity(IdMixin, TimestampMixin, Base):
    __tablename__ = "oidc_identity"
    __table_args__ = (UniqueConstraint("tenant_id", "provider_id", "subject"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("oidc_provider.id", ondelete="CASCADE")
    )
    subject: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))


class ApiKey(IdMixin, TimestampMixin, Base):
    __tablename__ = "api_key"
    __table_args__ = (UniqueConstraint("key_hash"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64))
    service_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
