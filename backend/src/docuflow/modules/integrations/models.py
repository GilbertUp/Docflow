"""Outbound webhooks and retry-safe writes (F20)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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


class WebhookSubscription(IdMixin, TimestampMixin, Base):
    """An address that is told when chosen things happen. The secret signs every call."""

    __tablename__ = "webhook_subscription"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_webhook_subscription_name"),
        CheckConstraint("status IN ('active', 'disabled')", name="status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(500))
    events: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # The signing secret, encrypted with the organization's key. It is shown once, when made.
    secret_wrapped: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class WebhookDelivery(IdMixin, TimestampMixin, Base):
    """One event to one address, and what happened when it was sent."""

    __tablename__ = "webhook_delivery"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="status"),
        Index("ix_webhook_delivery_subscription", "tenant_id", "subscription_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook_subscription.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String(120))
    # What is sent: the event's id, type, time and the object it is about. Never document text.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redelivery_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("webhook_delivery.id"))


class IdempotencyRecord(IdMixin, Base):
    """A request that carried an Idempotency-Key, and the answer it got.

    Not tied to one organization, because it is looked up before anyone is signed in to a
    request, so only the platform role can reach it. The caller is a hash of their credential, so
    a key from one caller can never replay another's response.
    """

    __tablename__ = "idempotency_record"
    __table_args__ = (
        UniqueConstraint("caller_hash", "key", name="uq_idempotency_caller_key"),
        CheckConstraint("state IN ('processing', 'complete')", name="state"),
        Index("ix_idempotency_created", "created_at"),
    )

    caller_hash: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="processing")
    status_code: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(120))
    body: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
