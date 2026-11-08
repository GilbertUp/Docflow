"""F14 over HTTP: approvals, signatures and stamps tied to the file they were made on."""

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
from tests.identity.support import STRONG, assign_role, audit_events, new_role, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    return make_app(db, audit, keys, MemoryObjectStore(), max_upload_bytes=1024 * 1024)


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss = boss_of(db, tenant)
    role = new_role(db, tenant, "Flow admin", ("workflows.manage",))
    assign_role(db, tenant, role, user_id=boss.user_id)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Invoices")
    document = put(client, csrf, folder["id"], "invoice.pdf", text_pdf(["An invoice"])).json()[
        "document"
    ]
    return {
        "id": str(boss.user_id),
        "client": client,
        "csrf": csrf,
        "folder": folder,
        "document": document,
    }


def person(
    app: FastAPI,
    db: Database,
    tenant: uuid.UUID,
    office: dict[str, Any],
    name: str,
    *rights: str,
) -> dict[str, Any]:
    user_id = new_user(db, tenant, name)
    allow(
        db,
        tenant,
        uuid.UUID(office["folder"]["acl_anchor_id"]),
        user_id,
        *(rights or ("read", "preview")),
    )
    client, csrf = sign_in(app, db, tenant, name)
    return {"id": str(user_id), "client": client, "csrf": csrf}


def sign(who: dict[str, Any], document_id: str, password: str = STRONG, statement: str = ""):
    return who["client"].post(
        f"{API}/documents/{document_id}/signatures",
        json={"password": password, "statement": statement},
        headers=who["csrf"],
    )


# Signatures


def test_signing_needs_the_password_again_and_binds_to_the_file(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    wrong = sign(office, doc, "not the password")
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "bad_password"
    assert office["client"].get(f"{API}/documents/{doc}/signatures").json() == []

    done = sign(office, doc, statement="Checked against the order")
    assert done.status_code == 201, done.text
    body = done.json()
    assert body["kind"] == "internal_attestation"  # never passed off as an external e-signature
    assert (body["status"], body["current"], body["intact"]) == ("valid", True, True)
    assert len(body["bound_hash"]) == 64
    assert body["statement"] == "Checked against the order"
    assert len(audit_events(db, tenant, "signature.created")) == 1
    assert len(audit_events(db, tenant, "signature.refused")) == 1

    assert sign(office, doc).status_code == 409  # already signed this version


def test_a_new_version_is_not_covered_by_an_old_signature(
    office: dict[str, Any],
) -> None:
    client, csrf = office["client"], office["csrf"]
    doc = office["document"]["id"]
    sign(office, doc)
    path = f"{API}/documents/{doc}"
    assert add(client, csrf, path, "invoice.pdf", text_pdf(["Changed"]), "Edit").status_code == 201
    old = client.get(f"{path}/signatures").json()[0]
    assert (old["version_number"], old["current"], old["intact"]) == (1, False, True)
    assert sign(office, doc).status_code == 201  # the new version can be signed
    versions = {
        s["version_number"]: s["bound_hash"] for s in client.get(f"{path}/signatures").json()
    }
    assert versions[1] != versions[2]


def test_repeated_wrong_passwords_lock_the_account(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    clerk = person(app, db, tenant, office, "clerk")
    doc = office["document"]["id"]
    for _ in range(5):
        assert sign(clerk, doc, "nope").status_code == 403
    locked = sign(clerk, doc, STRONG)  # even the right one is refused now
    assert locked.status_code == 403
    assert clerk["client"].get(f"{API}/documents/{doc}/signatures").json() == []


def test_signing_needs_the_right_to_see_the_content(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    reader = person(app, db, tenant, office, "reader", "read")
    refused = sign(reader, doc)
    assert refused.status_code == 403
    new_user(db, tenant, "stranger")
    client, csrf = sign_in(app, db, tenant, "stranger")
    stranger = {"client": client, "csrf": csrf}
    assert sign(stranger, doc).status_code == 404


def test_only_the_signer_can_revoke_and_must_say_why(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    other = person(app, db, tenant, office, "other")
    made = sign(office, doc).json()
    url = f"{API}/signatures/{made['id']}/revoke"
    assert (
        other["client"].post(url, json={"reason": "No"}, headers=other["csrf"]).status_code == 403
    )
    revoked = office["client"].post(
        url, json={"reason": "Signed the wrong one"}, headers=office["csrf"]
    )
    assert revoked.status_code == 200, revoked.text
    assert (revoked.json()["status"], revoked.json()["current"]) == ("revoked", False)
    assert revoked.json()["revoked_reason"] == "Signed the wrong one"
    again = office["client"].post(url, json={"reason": "Twice"}, headers=office["csrf"])
    assert again.status_code == 409
    assert len(audit_events(db, tenant, "signature.revoked")) == 1
    assert sign(office, doc).status_code == 201  # and a fresh signature is allowed afterwards


# Approvals


def test_a_workflow_approval_is_recorded_against_the_file_it_was_decided_on(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    alice = person(app, db, tenant, office, "alice")
    made = office["client"].post(
        f"{API}/workflows",
        json={
            "name": "Approval",
            "steps": [{"name": "Check", "kind": "approval", "assignee_id": alice["id"]}],
        },
        headers=office["csrf"],
    )
    doc = office["document"]["id"]
    started = office["client"].post(
        f"{API}/documents/{doc}/workflows",
        json={"workflow_id": made.json()["id"]},
        headers=office["csrf"],
    )
    step = started.json()["steps"][0]["id"]
    decided = alice["client"].post(
        f"{API}/workflow-steps/{step}/decision",
        json={"decision": "approve", "comment": "Fine"},
        headers=alice["csrf"],
    )
    assert decided.status_code == 200, decided.text

    approvals = office["client"].get(f"{API}/documents/{doc}/approvals").json()
    assert len(approvals) == 1
    row = approvals[0]
    assert (row["decision"], row["reason"], row["decider_name"]) == ("approve", "Fine", "alice")
    assert (row["version_number"], row["current"], len(row["version_hash"])) == (1, True, 64)

    add(
        office["client"],
        office["csrf"],
        f"{API}/documents/{doc}",
        "invoice.pdf",
        text_pdf(["Changed after approval"]),
        "Edit",
    )
    after = office["client"].get(f"{API}/documents/{doc}/approvals").json()[0]
    assert after["current"] is False  # the approval does not carry over to the new file


# Stamps


def make_stamp(office: dict[str, Any], name: str = "Approved", text: str = "APPROVED"):
    return office["client"].post(
        f"{API}/stamps",
        json={"name": name, "text": text, "color": "green"},
        headers=office["csrf"],
    )


def test_only_a_manager_makes_stamps_and_names_are_unique(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    clerk = person(app, db, tenant, office, "clerk")
    refused = clerk["client"].post(
        f"{API}/stamps", json={"name": "X", "text": "X"}, headers=clerk["csrf"]
    )
    assert refused.status_code == 403
    assert make_stamp(office).status_code == 201
    assert make_stamp(office).status_code == 409
    assert clerk["client"].get(f"{API}/stamps").status_code == 200
    bad = office["client"].post(
        f"{API}/stamps", json={"name": "Odd", "text": "x" * 31}, headers=office["csrf"]
    )
    assert bad.status_code == 422


def test_a_stamp_is_drawn_on_the_page_image_and_never_changes_the_file(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf = office["client"], office["csrf"]
    doc = office["document"]["id"]
    stamp = make_stamp(office).json()
    page = f"{API}/documents/{doc}/preview/pages/1"
    before = client.get(page, params={"width": 500}).content

    placed = client.post(
        f"{API}/documents/{doc}/stamps",
        json={"stamp_id": stamp["id"], "page": 1, "x": 0.5, "y": 0.3},
        headers=csrf,
    )
    assert placed.status_code == 201, placed.text
    after = client.get(page, params={"width": 500}).content
    assert after != before  # the mark is on the image
    listed = client.get(f"{API}/documents/{doc}/stamps").json()
    assert [(s["text"], s["page"], s["current"]) for s in listed] == [("APPROVED", 1, True)]
    assert len(audit_events(db, tenant, "document.stamped")) == 1

    # Archiving the template does not change what was already applied.
    client.patch(f"{API}/stamps/{stamp['id']}", json={"status": "archived"}, headers=csrf)
    assert client.get(page, params={"width": 500}).content == after
    again = client.post(
        f"{API}/documents/{doc}/stamps",
        json={"stamp_id": stamp["id"], "page": 1, "x": 0.1, "y": 0.1},
        headers=csrf,
    )
    assert again.status_code == 404

    removed = client.delete(f"{API}/documents/{doc}/stamps/{placed.json()['id']}", headers=csrf)
    assert removed.status_code == 204
    assert client.get(page, params={"width": 500}).content == before


def test_stamps_need_the_annotate_right_and_a_place_on_the_page(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    stamp = make_stamp(office).json()
    reader = person(app, db, tenant, office, "reader", "read")
    body = {"stamp_id": stamp["id"], "page": 1, "x": 0.5, "y": 0.5}
    assert (
        reader["client"]
        .post(f"{API}/documents/{doc}/stamps", json=body, headers=reader["csrf"])
        .status_code
        == 403
    )
    outside = office["client"].post(
        f"{API}/documents/{doc}/stamps", json={**body, "x": 1.5}, headers=office["csrf"]
    )
    assert outside.status_code == 422

    marker = person(app, db, tenant, office, "marker", "read", "annotate")
    mine = marker["client"].post(f"{API}/documents/{doc}/stamps", json=body, headers=marker["csrf"])
    assert mine.status_code == 201
    other = person(app, db, tenant, office, "other", "read", "annotate")
    url = f"{API}/documents/{doc}/stamps/{mine.json()['id']}"
    assert other["client"].delete(url, headers=other["csrf"]).status_code == 403
    assert marker["client"].delete(url, headers=marker["csrf"]).status_code == 204
