"""Tamper-evident audit trail (ADR 0012).

Events are written in the caller's transaction, so an action and its record commit together.
Each hash covers the event and its predecessor, keyed by a secret held outside the database,
so someone with full database access cannot forge a replacement chain.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO, Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from docuflow.platform import context
from docuflow.platform.db import utcnow
from docuflow.platform.models import AuditCheckpoint, AuditEvent

GENESIS_HASH = "0" * 64
REDACTED = "[REDACTED]"
SENSITIVE_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "private_key",
    "recovery_code",
    "totp",
)


def redact(value: Any) -> Any:
    """Remove secret values by key name, at any depth."""
    if isinstance(value, dict):
        return {
            key: REDACTED
            if any(marker in str(key).lower() for marker in SENSITIVE_MARKERS)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


def tenant_audit_key(root_key: bytes, tenant_id: uuid.UUID) -> bytes:
    return hmac.new(root_key, b"audit:" + tenant_id.bytes, hashlib.sha256).digest()


def _canonical(fields: dict[str, Any]) -> bytes:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode()


def _event_fields(event: AuditEvent) -> dict[str, Any]:
    occurred_at: datetime = event.occurred_at
    return {
        "tenant_id": str(event.tenant_id),
        "seq": event.seq,
        "occurred_at": occurred_at.timestamp(),
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "actor_type": event.actor_type,
        "action": event.action,
        "object_type": event.object_type,
        "object_id": event.object_id,
        "result": event.result,
        "ip": event.ip,
        "device": event.device,
        "correlation_id": event.correlation_id,
        "details": event.details,
        "prev_hash": event.prev_hash,
    }


def compute_hash(key: bytes, event: AuditEvent) -> str:
    return hmac.new(key, _canonical(_event_fields(event)), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class AuditActor:
    actor_id: uuid.UUID | None = None
    actor_type: str = "system"
    ip: str | None = None
    device: str | None = None


class AuditLog:
    def __init__(self, root_key: bytes) -> None:
        self._root_key = root_key

    def record(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        action: str,
        *,
        actor: AuditActor | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        result: str = "success",
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        actor = actor or AuditActor()
        # Serialises writers per tenant until commit, which keeps the chain linear.
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"audit:{tenant_id}"},
        )
        last = session.execute(
            select(AuditEvent.seq, AuditEvent.hash)
            .where(AuditEvent.tenant_id == tenant_id)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        ).first()
        event = AuditEvent(
            tenant_id=tenant_id,
            seq=last.seq + 1 if last else 1,
            occurred_at=utcnow(),
            actor_id=actor.actor_id,
            actor_type=actor.actor_type,
            action=action,
            object_type=object_type,
            object_id=object_id,
            result=result,
            ip=actor.ip,
            device=actor.device,
            correlation_id=context.correlation_id.get(),
            # Round-trip now, so the hash covers exactly what JSONB will return later.
            details=json.loads(json.dumps(redact(details or {}), default=str)),
            prev_hash=last.hash if last else GENESIS_HASH,
            hash="",
        )
        event.hash = compute_hash(tenant_audit_key(self._root_key, tenant_id), event)
        session.add(event)
        session.flush()
        return event

    def verify(self, session: Session, tenant_id: uuid.UUID) -> VerificationResult:
        """Walk the chain in order and report the first event that does not verify."""
        key = tenant_audit_key(self._root_key, tenant_id)
        previous = GENESIS_HASH
        expected_seq = 1
        checked = 0
        rows = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant_id)
            .order_by(AuditEvent.seq)
            .execution_options(yield_per=500)
        )
        for event in rows:
            if event.seq != expected_seq:
                return VerificationResult(False, checked, expected_seq, "missing event")
            if event.prev_hash != previous:
                return VerificationResult(False, checked, event.seq, "broken link")
            if not hmac.compare_digest(event.hash, compute_hash(key, event)):
                return VerificationResult(False, checked, event.seq, "content changed")
            previous = event.hash
            expected_seq += 1
            checked += 1
        return VerificationResult(True, checked, None, None, head_hash=previous)

    def checkpoint_document(self, tenant_id: uuid.UUID, seq: int, head_hash: str) -> bytes:
        """Signed statement of the chain head, stored outside the database."""
        body = {"tenant_id": str(tenant_id), "seq": seq, "head_hash": head_hash}
        key = tenant_audit_key(self._root_key, tenant_id)
        body["signature"] = hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()
        return _canonical(body)

    def write_checkpoint(
        self, session: Session, tenant_id: uuid.UUID, store: CheckpointStore
    ) -> AuditCheckpoint | None:
        """Anchor the current head externally. A rewrite of history then has a witness."""
        head = session.execute(
            select(AuditEvent.seq, AuditEvent.hash)
            .where(AuditEvent.tenant_id == tenant_id)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        ).first()
        if head is None:
            return None
        existing = session.scalar(
            select(AuditCheckpoint).where(
                AuditCheckpoint.tenant_id == tenant_id, AuditCheckpoint.seq == head.seq
            )
        )
        if existing is not None:
            return existing
        key = f"audit-checkpoints/{tenant_id}/{head.seq:020d}.json"
        document = self.checkpoint_document(tenant_id, head.seq, head.hash)
        store.put(key, io.BytesIO(document), len(document))
        checkpoint = AuditCheckpoint(
            tenant_id=tenant_id, seq=head.seq, head_hash=head.hash, object_key=key
        )
        session.add(checkpoint)
        session.flush()
        return checkpoint

    def verify_checkpoints(
        self, session: Session, tenant_id: uuid.UUID, store: CheckpointReader
    ) -> VerificationResult:
        """Compare database events with the externally stored statements."""
        checkpoints = session.scalars(
            select(AuditCheckpoint)
            .where(AuditCheckpoint.tenant_id == tenant_id)
            .order_by(AuditCheckpoint.seq)
        )
        checked = 0
        for checkpoint in checkpoints:
            current = session.scalar(
                select(AuditEvent.hash).where(
                    AuditEvent.tenant_id == tenant_id, AuditEvent.seq == checkpoint.seq
                )
            )
            expected = self.checkpoint_document(tenant_id, checkpoint.seq, current or "")
            stored = b"".join(store.get(checkpoint.object_key))
            if current is None or not hmac.compare_digest(stored, expected):
                return VerificationResult(False, checked, checkpoint.seq, "checkpoint mismatch")
            checked += 1
        return VerificationResult(True, checked, None, None)


class CheckpointStore(Protocol):
    def put(self, key: str, body: BinaryIO, length: int) -> None: ...


class CheckpointReader(Protocol):
    def get(self, key: str) -> Iterator[bytes]: ...


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    checked: int
    first_bad_seq: int | None
    reason: str | None
    head_hash: str | None = None
