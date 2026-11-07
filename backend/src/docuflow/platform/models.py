"""Platform tables. Business modules define their own tables."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.platform.db import Base, IdMixin, TimestampMixin, utcnow

JOB_STATUSES = ("queued", "running", "succeeded", "failed", "dead")
TIMER_STATUSES = ("pending", "fired", "cancelled")


class Tenant(IdMixin, TimestampMixin, Base):
    __tablename__ = "tenant"
    __table_args__ = (CheckConstraint("status IN ('active', 'suspended')", name="status"),)

    name: Mapped[str] = mapped_column(String(200), unique=True)
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Kigali")
    permission_epoch: Mapped[int] = mapped_column(BigInteger, default=0)


class SystemConfig(IdMixin, TimestampMixin, Base):
    __tablename__ = "system_config"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    key: Mapped[str] = mapped_column(String(120))
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ProcessingJob(IdMixin, TimestampMixin, Base):
    """Source of truth for background work (ADR 0007)."""

    __tablename__ = "processing_job"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'dead')", name="status"
        ),
        Index("ix_processing_job_tenant_status", "tenant_id", "status", "next_run_at"),
        Index("ix_processing_job_running", "status", "heartbeat_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    type: Mapped[str] = mapped_column(String(80))
    queue: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Outbox(IdMixin, TimestampMixin, Base):
    """Written in the business transaction, so an event exists only if that commit did."""

    __tablename__ = "outbox"
    __table_args__ = (
        Index(
            "ix_outbox_unpublished",
            "created_at",
            postgresql_where="published_at IS NULL",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    topic: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Timer(IdMixin, TimestampMixin, Base):
    """Durable scheduled action. Survives restarts because it lives in PostgreSQL."""

    __tablename__ = "timer"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        CheckConstraint("status IN ('pending', 'fired', 'cancelled')", name="status"),
        Index("ix_timer_due", "fire_at", postgresql_where="status = 'pending'"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    kind: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")


class AuditEvent(IdMixin, Base):
    """Append-only, hash-chained record (ADR 0012). Updates are rejected by a trigger."""

    __tablename__ = "audit_event"
    __table_args__ = (
        UniqueConstraint("tenant_id", "seq"),
        Index("ix_audit_event_object", "tenant_id", "object_type", "object_id"),
        Index("ix_audit_event_actor", "tenant_id", "actor_id", "occurred_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    seq: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    actor_type: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(120))
    object_type: Mapped[str | None] = mapped_column(String(80))
    object_id: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(20))
    ip: Mapped[str | None] = mapped_column(String(64))
    device: Mapped[str | None] = mapped_column(String(255))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class AuditCheckpoint(IdMixin, TimestampMixin, Base):
    __tablename__ = "audit_checkpoint"
    __table_args__ = (UniqueConstraint("tenant_id", "seq"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    seq: Mapped[int] = mapped_column(BigInteger)
    head_hash: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(255))
