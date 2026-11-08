"""F16 over HTTP: finding, exporting and checking the audit trail."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import update

from docuflow.modules.audit.service import safe_cell
from docuflow.modules.identity.models import User
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from tests.documents.files import text_pdf
from tests.documents.test_upload import make, put
from tests.documents.test_versions import add
from tests.identity.support import assign_role, audit_events, new_role, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider, store: MemoryObjectStore) -> FastAPI:
    return make_app(db, audit, keys, store, max_upload_bytes=1024 * 1024)


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss = boss_of(db, tenant)
    role = new_role(db, tenant, "Auditor", ("audit.read",))
    assign_role(db, tenant, role, user_id=boss.user_id)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Invoices")
    document = put(client, csrf, folder["id"], "a.pdf", text_pdf(["An invoice"])).json()["document"]
    add(client, csrf, f"{API}/documents/{document['id']}", "a.pdf", text_pdf(["Changed"]), "Edit")
    client.post(f"{API}/documents/{document['id']}/comments", json={"body": "Hello"}, headers=csrf)
    return {
        "id": str(boss.user_id),
        "client": client,
        "csrf": csrf,
        "folder": folder,
        "document": document,
    }


def events(office: dict[str, Any], **params: Any) -> dict[str, Any]:
    response = office["client"].get(f"{API}/audit/events", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_only_someone_with_audit_read_can_see_the_trail(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    new_user(db, tenant, "clerk")
    clerk, csrf = sign_in(app, db, tenant, "clerk")
    for path in ("/audit/events", "/audit/export", f"/documents/{doc}/audit"):
        assert clerk.get(f"{API}{path}").status_code == 403, path
    assert clerk.post(f"{API}/audit/verify", headers=csrf).status_code == 403


def test_events_are_found_by_action_object_actor_and_time_and_paged(
    office: dict[str, Any],
) -> None:
    doc = office["document"]["id"]
    everything = events(office, limit=200)
    assert everything["events"][0]["seq"] > everything["events"][-1]["seq"]  # newest first
    assert all(e["label"] for e in everything["events"])

    versions = events(office, action="document.version_added")
    assert [e["action"] for e in versions["events"]] == ["document.version_added"] * len(
        versions["events"]
    )
    assert len(versions["events"]) >= 1
    docs = events(office, object_type="document", object_id=doc)["events"]
    assert docs
    assert all(e["object_id"] == doc for e in docs)
    mine = events(office, actor_id=office["id"], action_prefix="document.")["events"]
    assert mine
    assert all(e["actor_name"] == "boss" for e in mine)
    assert all(e["action"].startswith("document.") for e in mine)

    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert events(office, since=future)["events"] == []
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert len(events(office, since=past, limit=200)["events"]) == len(everything["events"])
    reversed_ = office["client"].get(f"{API}/audit/events", params={"since": future, "until": past})
    assert reversed_.status_code == 400

    first = events(office, limit=3)
    assert len(first["events"]) == 3
    assert first["next_before_seq"] == first["events"][-1]["seq"]
    second = events(office, limit=3, before_seq=first["next_before_seq"])
    assert second["events"][0]["seq"] < first["events"][-1]["seq"]
    assert events(office, result="failure")["events"] == []


def test_a_prefix_is_not_a_pattern(office: dict[str, Any]) -> None:
    assert events(office, action_prefix="%")["events"] == []
    assert events(office, action_prefix="_")["events"] == []


def test_a_documents_timeline_shows_what_happened_to_it(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    timeline = office["client"].get(f"{API}/documents/{doc}/audit").json()
    actions = [e["action"] for e in timeline]
    assert "document.version_added" in actions
    assert "document.commented" in actions
    assert all(e["object_id"] == doc for e in timeline)

    # Holding audit.read is not enough to see a document the person cannot read.
    auditor_id = new_user(db, tenant, "auditor")
    role = new_role(db, tenant, "Auditor 2", ("audit.read",))
    assign_role(db, tenant, role, user_id=auditor_id)
    auditor, _csrf = sign_in(app, db, tenant, "auditor")
    assert auditor.get(f"{API}/documents/{doc}/audit").status_code == 404
    allow(db, tenant, uuid.UUID(office["folder"]["acl_anchor_id"]), auditor_id, "read")
    assert auditor.get(f"{API}/documents/{doc}/audit").status_code == 200


def test_the_export_is_csv_with_the_chain_and_is_itself_recorded(
    office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    response = office["client"].get(f"{API}/audit/export", params={"action_prefix": "document."})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["cache-control"] == "private, no-store"
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows
    assert int(response.headers["x-export-events"]) == len(rows)
    assert response.headers["x-export-truncated"] == "false"
    assert len(response.headers["x-chain-head-hash"]) == 64
    assert [int(r["seq"]) for r in rows] == sorted(int(r["seq"]) for r in rows)
    assert {"prev_hash", "hash", "details", "actor_name"} <= set(rows[0])
    assert all(len(r["hash"]) == 64 for r in rows)

    recorded = audit_events(db, tenant, "audit.exported")
    assert len(recorded) == 1
    assert recorded[0].details["filters"] == {"action_prefix": "document."}
    assert recorded[0].details["events"] == len(rows)


def test_a_spreadsheet_formula_in_the_trail_is_neutralised(
    office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    assert safe_cell("=SUM(1)") == "'=SUM(1)"
    assert safe_cell("+1") == "'+1"
    assert safe_cell("-1") == "'-1"
    assert safe_cell("@x") == "'@x"
    assert safe_cell("plain") == "plain"
    assert safe_cell(None) == ""
    with db.tenant_session(tenant) as session:
        session.execute(
            update(User)
            .where(User.id == uuid.UUID(office["id"]))
            .values(display_name='=HYPERLINK("http://evil","click")')
        )
    text = office["client"].get(f"{API}/audit/export", params={"action_prefix": "document."}).text
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["actor_name"].startswith("'=HYPERLINK")


def test_verification_checks_the_chain_and_the_external_checkpoints(
    app: FastAPI,
    office: dict[str, Any],
    db: Database,
    tenant: uuid.UUID,
    audit: AuditLog,
    store: MemoryObjectStore,
) -> None:
    client, csrf = office["client"], office["csrf"]
    first = client.post(f"{API}/audit/verify", headers=csrf).json()
    assert first["ok"] is True
    assert first["events"] > 0
    assert (first["checkpoints"], first["checkpoints_ok"]) == (0, True)

    with db.tenant_session(tenant) as session:
        checkpoint = audit.write_checkpoint(session, tenant, store)
        assert checkpoint is not None
        key = checkpoint.object_key
    second = client.post(f"{API}/audit/verify", headers=csrf).json()
    assert second["ok"] is True
    assert (second["checkpoints"], second["checkpoints_ok"]) == (1, True)
    assert len(audit_events(db, tenant, "audit.verified")) == 2

    store.objects[key] = b"{}"  # someone alters the externally held statement
    broken = client.post(f"{API}/audit/verify", headers=csrf).json()
    assert broken["ok"] is False
    assert broken["checkpoints_ok"] is False
    assert broken["checkpoint_reason"] == "checkpoint mismatch"
    assert broken["reason"] is None  # the chain itself is intact
