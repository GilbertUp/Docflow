"""ADR 0012: completeness, tamper detection and redaction."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from typing import BinaryIO

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from docuflow.platform.audit import REDACTED, AuditLog, redact
from docuflow.platform.db import Database


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, body: BinaryIO, length: int) -> None:
        self.objects[key] = body.read(length)

    def get(self, key: str) -> Iterator[bytes]:
        return iter([self.objects[key]])


def write_events(db: Database, audit: AuditLog, tenant: uuid.UUID, count: int = 3) -> None:
    for index in range(count):
        with db.tenant_session(tenant) as session:
            audit.record(session, tenant, "document.read", object_id=str(index))


def force_edit(engines: dict[str, Engine], statement: str) -> None:
    """Simulates an attacker with superuser access who disables the guard trigger."""
    with engines["admin"].begin() as connection:
        connection.execute(text("ALTER TABLE audit_event DISABLE TRIGGER USER"))
        connection.execute(text(statement))
        connection.execute(text("ALTER TABLE audit_event ENABLE TRIGGER USER"))


def test_redaction_is_recursive() -> None:
    result = redact({"name": "x", "nested": [{"Password": "p", "api_key": "k"}], "token": "t"})
    assert result == {
        "name": "x",
        "nested": [{"Password": REDACTED, "api_key": REDACTED}],
        "token": REDACTED,
    }


@pytest.mark.db
def test_chain_verifies_and_sequence_is_contiguous(
    db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    write_events(db, audit, tenant, 5)
    with db.tenant_session(tenant) as session:
        result = audit.verify(session, tenant)
    assert result.ok
    assert result.checked == 5


@pytest.mark.db
def test_chains_are_separate_per_tenant(
    db: Database, audit: AuditLog, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    write_events(db, audit, tenant, 2)
    write_events(db, audit, other_tenant, 1)
    with db.tenant_session(other_tenant) as session:
        assert audit.verify(session, other_tenant).checked == 1


@pytest.mark.db
def test_rolled_back_action_leaves_no_event(
    db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    with pytest.raises(RuntimeError), db.tenant_session(tenant) as session:  # noqa: PT012
        audit.record(session, tenant, "document.delete")
        raise RuntimeError("action failed")
    with db.tenant_session(tenant) as session:
        assert audit.verify(session, tenant).checked == 0


@pytest.mark.db
@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_event SET action = 'x'", "DELETE FROM audit_event"],
)
def test_runtime_role_cannot_alter_audit(
    db: Database, audit: AuditLog, tenant: uuid.UUID, statement: str
) -> None:
    write_events(db, audit, tenant, 1)
    with pytest.raises(ProgrammingError, match="permission denied"):
        with db.tenant_session(tenant) as session:
            session.execute(text(statement))


@pytest.mark.db
def test_trigger_stops_even_superusers(
    engines: dict[str, Engine], db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    write_events(db, audit, tenant, 1)
    with pytest.raises(DBAPIError, match="append-only"), engines["admin"].begin() as connection:
        connection.execute(text("UPDATE audit_event SET action = 'x'"))


@pytest.mark.db
def test_detects_modified_content(
    engines: dict[str, Engine], db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    write_events(db, audit, tenant, 3)
    force_edit(
        engines,
        f"UPDATE audit_event SET action = 'forged' WHERE tenant_id = '{tenant}' AND seq = 2",
    )
    with db.tenant_session(tenant) as session:
        result = audit.verify(session, tenant)
    assert (result.ok, result.first_bad_seq, result.reason) == (False, 2, "content changed")


@pytest.mark.db
def test_detects_deleted_event(
    engines: dict[str, Engine], db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    write_events(db, audit, tenant, 3)
    force_edit(engines, f"DELETE FROM audit_event WHERE tenant_id = '{tenant}' AND seq = 2")
    with db.tenant_session(tenant) as session:
        result = audit.verify(session, tenant)
    assert (result.ok, result.first_bad_seq, result.reason) == (False, 2, "missing event")


@pytest.mark.db
def test_attacker_without_key_cannot_forge_a_replacement(
    engines: dict[str, Engine], db: Database, tenant: uuid.UUID, audit: AuditLog
) -> None:
    """A rewrite hashed with a guessed key fails, because the real key is outside the database."""
    write_events(db, audit, tenant, 1)
    with db.tenant_session(tenant) as session:
        forged = AuditLog(b"attacker-guess" * 3)
        forged.record(session, tenant, "document.forged")
    with db.tenant_session(tenant) as session:
        result = audit.verify(session, tenant)
    assert (result.ok, result.first_bad_seq) == (False, 2)


@pytest.mark.db
def test_checkpoint_detects_truncated_history(
    engines: dict[str, Engine], db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    """Deleting the newest events leaves a valid chain; only the outside witness notices."""
    store = MemoryStore()
    write_events(db, audit, tenant, 3)
    with db.tenant_session(tenant) as session:
        assert audit.write_checkpoint(session, tenant, store) is not None
    with db.tenant_session(tenant) as session:
        assert audit.verify_checkpoints(session, tenant, store).ok
    force_edit(engines, f"DELETE FROM audit_event WHERE tenant_id = '{tenant}' AND seq = 3")
    with db.tenant_session(tenant) as session:
        assert audit.verify(session, tenant).ok
        result = audit.verify_checkpoints(session, tenant, store)
    assert (result.ok, result.first_bad_seq) == (False, 3)


@pytest.mark.db
def test_checkpoint_is_idempotent(db: Database, audit: AuditLog, tenant: uuid.UUID) -> None:
    store = MemoryStore()
    write_events(db, audit, tenant, 1)
    for _ in range(2):
        with db.tenant_session(tenant) as session:
            audit.write_checkpoint(session, tenant, store)
    assert len(store.objects) == 1


def test_memory_store_contract() -> None:
    store = MemoryStore()
    store.put("k", io.BytesIO(b"abc"), 3)
    assert b"".join(store.get("k")) == b"abc"
