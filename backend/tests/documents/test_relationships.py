"""F09 over HTTP: restoring an earlier version, and typed links between documents."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI

from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from tests.documents.files import text_pdf
from tests.documents.test_upload import make, put
from tests.documents.test_versions import add
from tests.identity.support import audit_events, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    return make_app(db, audit, keys, MemoryObjectStore(), max_upload_bytes=1024 * 1024)


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss_of(db, tenant)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Invoices")
    return {"client": client, "csrf": csrf, "folder": folder}


def upload(office: dict[str, Any], name: str, text: str) -> dict[str, Any]:
    response = put(office["client"], office["csrf"], office["folder"]["id"], name, text_pdf([text]))
    assert response.status_code == 201, response.text
    return response.json()["document"]


def link(office: dict[str, Any], source: str, target: str, kind: str):
    return office["client"].post(
        f"{API}/documents/{source}/links",
        json={"to_document_id": target, "relation_type": kind},
        headers=office["csrf"],
    )


# Restoring a version


def test_an_earlier_version_is_restored_as_a_new_one(
    office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf = office["client"], office["csrf"]
    document = upload(office, "contract.pdf", "First wording of the contract")
    path = f"{API}/documents/{document['id']}"
    second = add(client, csrf, path, "contract.pdf", text_pdf(["Second wording"]), "Edited")
    assert second.status_code == 201, second.text

    restored = client.post(f"{path}/versions/1/restore", headers=csrf)
    assert restored.status_code == 201, restored.text
    body = restored.json()
    assert body["version"]["number"] == 3
    assert body["version"]["reason"] == "Restored from version 1"
    versions = client.get(f"{path}/versions").json()
    assert [item["number"] for item in versions] == [1, 2, 3]  # history is kept, never rewritten
    assert versions[2]["size_bytes"] == versions[0]["size_bytes"]
    assert versions[2]["size_bytes"] != versions[1]["size_bytes"]

    (event,) = audit_events(db, tenant, "document.version_restored")
    assert event.details["restored_from"] == 1


def test_a_version_that_cannot_be_restored_is_refused_with_a_reason(
    office: dict[str, Any],
) -> None:
    client, csrf = office["client"], office["csrf"]
    document = upload(office, "contract.pdf", "Only wording")
    path = f"{API}/documents/{document['id']}"
    same = client.post(f"{path}/versions/1/restore", headers=csrf)
    assert same.status_code == 409
    assert "already the current" in same.json()["error"]["message"]
    assert client.post(f"{path}/versions/9/restore", headers=csrf).status_code == 404


def test_restoring_needs_the_version_right(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    document = upload(office, "contract.pdf", "First wording")
    path = f"{API}/documents/{document['id']}"
    add(office["client"], office["csrf"], path, "contract.pdf", text_pdf(["Second"]), "")
    anchor = uuid.UUID(office["folder"]["acl_anchor_id"])
    reader = new_user(db, tenant, "reader")
    allow(db, tenant, anchor, reader, "read")
    client, csrf = sign_in(app, db, tenant, "reader")
    assert client.post(f"{path}/versions/1/restore", headers=csrf).status_code == 403


# Links


def test_a_link_reads_both_ways_and_can_be_removed(
    office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf = office["client"], office["csrf"]
    invoice = upload(office, "invoice.pdf", "Invoice for order 77")
    order = upload(office, "order.pdf", "Purchase order 77")
    made = link(office, invoice["id"], order["id"], "responds_to")
    assert made.status_code == 201, made.text
    assert made.json()["wording"] == "Responds to"

    from_side = client.get(f"{API}/documents/{invoice['id']}/links").json()
    to_side = client.get(f"{API}/documents/{order['id']}/links").json()
    assert [(item["wording"], item["direction"]) for item in from_side] == [("Responds to", "from")]
    assert [(item["wording"], item["direction"]) for item in to_side] == [("Answered by", "to")]
    assert to_side[0]["document"]["title"] == "invoice.pdf"
    assert len(audit_events(db, tenant, "document.link_added")) == 2

    gone = client.delete(f"{API}/documents/{order['id']}/links/{made.json()['id']}", headers=csrf)
    assert gone.status_code == 204
    assert client.get(f"{API}/documents/{invoice['id']}/links").json() == []
    assert len(audit_events(db, tenant, "document.link_removed")) == 2


def test_bad_links_are_refused(office: dict[str, Any]) -> None:
    first = upload(office, "a.pdf", "Alpha document text")
    second = upload(office, "b.pdf", "Bravo document text")
    assert link(office, first["id"], first["id"], "related").status_code == 400
    assert link(office, first["id"], second["id"], "friends").status_code == 400
    assert link(office, first["id"], str(uuid.uuid4()), "related").status_code == 404
    assert link(office, first["id"], second["id"], "related").status_code == 201
    assert link(office, first["id"], second["id"], "related").status_code == 409
    # A "related" link means the same both ways, so the reverse is a duplicate too.
    assert link(office, second["id"], first["id"], "related").status_code == 409
    # A directional link in the reverse direction is a different statement.
    assert link(office, first["id"], second["id"], "supersedes").status_code == 201
    assert link(office, second["id"], first["id"], "supersedes").status_code == 201


def test_the_types_of_link_are_listed(office: dict[str, Any]) -> None:
    listed = office["client"].get(f"{API}/document-link-types").json()
    assert {item["type"] for item in listed} == {
        "related",
        "supersedes",
        "responds_to",
        "attached_to",
    }


def test_a_link_never_reveals_a_document_the_person_cannot_read(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    visible = upload(office, "visible.pdf", "A document anyone may see")
    hidden_folder = make(
        office["client"],
        office["csrf"],
        f"/cabinets/{office['folder']['cabinet_id']}/folders",
        "Board",
    )
    hidden = put(
        office["client"], office["csrf"], hidden_folder["id"], "hidden.pdf", text_pdf(["Secret"])
    ).json()["document"]
    assert link(office, visible["id"], hidden["id"], "related").status_code == 201

    reader = new_user(db, tenant, "reader")
    allow(
        db,
        tenant,
        uuid.UUID(office["folder"]["acl_anchor_id"]),
        reader,
        "read",
        "edit_metadata",
    )
    client, csrf = sign_in(app, db, tenant, "reader")
    assert client.get(f"{API}/documents/{visible['id']}/links").json() == []
    # And cannot be used to probe for it.
    probe = client.post(
        f"{API}/documents/{visible['id']}/links",
        json={"to_document_id": hidden["id"], "relation_type": "supersedes"},
        headers=csrf,
    )
    assert probe.status_code == 404


def test_making_a_link_needs_the_right_to_change_the_starting_document(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    first = upload(office, "a.pdf", "Alpha document text")
    second = upload(office, "b.pdf", "Bravo document text")
    reader = new_user(db, tenant, "reader")
    allow(db, tenant, uuid.UUID(office["folder"]["acl_anchor_id"]), reader, "read")
    client, csrf = sign_in(app, db, tenant, "reader")
    refused = client.post(
        f"{API}/documents/{first['id']}/links",
        json={"to_document_id": second["id"], "relation_type": "related"},
        headers=csrf,
    )
    assert refused.status_code == 403
