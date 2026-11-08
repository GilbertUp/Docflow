"""F06 over HTTP: types and schema versions, typing a document, and who may do what."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from docuflow.modules.indexing.models import IndexValue
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from tests.documents.test_upload import PDF, make, put
from tests.identity.support import assign_role, audit_events, new_role, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db

INVOICE_FIELDS: list[dict[str, Any]] = [
    {
        "key": "invoice_number",
        "label": "Invoice number",
        "data_type": "text",
        "required": True,
        "validation": {"pattern": r"INV-\d{4}"},
    },
    {
        "key": "total",
        "label": "Total",
        "data_type": "number",
        "required": True,
        "validation": {"min": 0},
    },
    {"key": "due_date", "label": "Due date", "data_type": "date"},
    {"key": "paid", "label": "Paid", "data_type": "boolean"},
    {
        "key": "status",
        "label": "Status",
        "data_type": "choice",
        "default_value": "open",
        "choices": [{"value": "open", "label": "Open"}, {"value": "closed", "label": "Closed"}],
    },
]


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    return make_app(db, audit, keys, MemoryObjectStore(), max_upload_bytes=1024 * 1024)


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    """An administrator of types who also owns a folder holding one document."""
    boss = boss_of(db, tenant)
    role = new_role(db, tenant, "Type admin", ("document_types.manage",))
    assign_role(db, tenant, role, user_id=boss.user_id)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Invoices")
    document = put(client, csrf, folder["id"], "invoice.pdf", PDF).json()["document"]
    return {
        "client": client,
        "csrf": csrf,
        "folder": folder,
        "document": document,
        "index": f"{API}/documents/{document['id']}/index",
    }


def make_type(office: dict[str, Any], name: str = "Supplier invoice", fields: Any = None):
    return office["client"].post(
        f"{API}/document-types",
        json={
            "name": name,
            "description": "From suppliers",
            "fields": INVOICE_FIELDS if fields is None else fields,
        },
        headers=office["csrf"],
    )


def put_index(office: dict[str, Any], type_id: str | None, values: dict[str, Any]):
    return office["client"].put(
        office["index"],
        json={"document_type_id": type_id, "values": values},
        headers=office["csrf"],
    )


GOOD = {"invoice_number": "INV-1042", "total": "1042.50", "due_date": "2026-11-30", "paid": "no"}


# Types


def test_a_type_is_created_with_its_first_set_of_fields(office: dict[str, Any]) -> None:
    created = make_type(office)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["name"], body["version"], body["status"]) == ("Supplier invoice", 1, "active")
    assert [field["key"] for field in body["fields"]] == [
        "invoice_number",
        "total",
        "due_date",
        "paid",
        "status",
    ]
    assert body["fields"][4]["choices"] == [
        {"value": "open", "label": "Open"},
        {"value": "closed", "label": "Closed"},
    ]
    listed = office["client"].get(f"{API}/document-types").json()
    assert [item["name"] for item in listed] == ["Supplier invoice"]
    assert office["client"].get(f"{API}/document-types/{body['id']}").json()["version"] == 1


def test_only_someone_with_the_right_can_change_types(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    made = make_type(office).json()
    new_user(db, tenant, "clerk")
    clerk, csrf = sign_in(app, db, tenant, "clerk")
    assert clerk.get(f"{API}/document-types").status_code == 200  # anyone signed in may see them
    refused = clerk.post(
        f"{API}/document-types", json={"name": "Mine", "fields": INVOICE_FIELDS}, headers=csrf
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "forbidden"
    assert (
        clerk.post(
            f"{API}/document-types/{made['id']}/versions",
            json={"row_version": made["row_version"], "fields": INVOICE_FIELDS[:2]},
            headers=csrf,
        ).status_code
        == 403
    )
    assert (
        clerk.patch(
            f"{API}/document-types/{made['id']}",
            json={"row_version": made["row_version"], "name": "Renamed"},
            headers=csrf,
        ).status_code
        == 403
    )


def test_names_are_unique_without_regard_to_case(office: dict[str, Any]) -> None:
    make_type(office, "Supplier invoice")
    again = make_type(office, "SUPPLIER INVOICE")
    assert again.status_code == 409
    assert "already exists" in again.json()["error"]["message"]


def test_every_bad_field_is_reported_at_once(office: dict[str, Any]) -> None:
    fields = [
        {"key": "Total", "label": "Total", "data_type": "number"},
        {"key": "ok", "label": "Fine", "data_type": "text"},
        {"key": "ok", "label": "Again", "data_type": "text"},
        {"key": "ref", "label": "Ref", "data_type": "text", "validation": {"pattern": "(a+)+"}},
    ]
    response = make_type(office, "Broken", fields)
    assert response.status_code == 422
    problems = {item["field"]: item["message"] for item in response.json()["error"]["fields"]}
    assert "lowercase" in problems["Total"]
    assert "Two fields" in problems["ok"]
    assert "repeats" in problems["ref"]
    assert office["client"].get(f"{API}/document-types").json() == []


def test_a_type_needs_at_least_one_field(office: dict[str, Any]) -> None:
    assert make_type(office, "Empty", []).status_code == 400
    too_many = [{"key": f"f{i}", "label": f"F{i}", "data_type": "text"} for i in range(41)]
    assert make_type(office, "Huge", too_many).status_code == 400


def test_changing_the_fields_publishes_a_new_version_and_keeps_the_old(
    office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf = office["client"], office["csrf"]
    made = make_type(office).json()
    fields = [*INVOICE_FIELDS, {"key": "po", "label": "PO number", "data_type": "text"}]
    published = client.post(
        f"{API}/document-types/{made['id']}/versions",
        json={"row_version": made["row_version"], "fields": fields},
        headers=csrf,
    )
    assert published.status_code == 201, published.text
    assert published.json()["version"] == 2
    assert [f["key"] for f in published.json()["fields"]][-1] == "po"
    (event,) = audit_events(db, tenant, "document_type.version_published")
    assert event.details["added"] == ["po"]

    stale = client.post(
        f"{API}/document-types/{made['id']}/versions",
        json={"row_version": made["row_version"], "fields": INVOICE_FIELDS[:2]},
        headers=csrf,
    )
    assert stale.status_code == 409
    same = client.post(
        f"{API}/document-types/{made['id']}/versions",
        json={"row_version": published.json()["row_version"], "fields": fields},
        headers=csrf,
    )
    assert same.status_code == 409
    assert "same as the current" in same.json()["error"]["message"]


def test_a_type_can_be_renamed_and_archived(office: dict[str, Any]) -> None:
    client, csrf = office["client"], office["csrf"]
    made = make_type(office).json()
    renamed = client.patch(
        f"{API}/document-types/{made['id']}",
        json={"row_version": made["row_version"], "name": "Vendor invoice"},
        headers=csrf,
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Vendor invoice"
    archived = client.patch(
        f"{API}/document-types/{made['id']}",
        json={"row_version": renamed.json()["row_version"], "status": "archived"},
        headers=csrf,
    )
    assert archived.json()["status"] == "archived"
    assert client.get(f"{API}/document-types").json() == []
    shown = client.get(f"{API}/document-types", params={"include_archived": True}).json()
    assert [item["name"] for item in shown] == ["Vendor invoice"]
    publish = client.post(
        f"{API}/document-types/{made['id']}/versions",
        json={"row_version": archived.json()["row_version"], "fields": INVOICE_FIELDS[:2]},
        headers=csrf,
    )
    assert publish.status_code == 409


# Documents


def test_typing_a_document_stores_typed_values_and_says_what_is_missing(
    office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    made = make_type(office).json()
    before = office["client"].get(office["index"]).json()
    assert (before["document_type_id"], before["fields"], before["complete"]) == (None, [], False)

    saved = put_index(office, made["id"], {"invoice_number": "INV-1042"})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["type_name"] == "Supplier invoice"
    assert (body["schema_version"], body["current_version"], body["outdated"]) == (1, 1, False)
    assert body["missing_required"] == ["total"]
    assert body["complete"] is False
    assert {f["key"]: f["value"] for f in body["fields"]}["status"] == "open"  # the default

    done = put_index(office, made["id"], GOOD).json()
    assert done["complete"] is True
    values = {f["key"]: f["value"] for f in done["fields"]}
    assert values == {
        "invoice_number": "INV-1042",
        "total": "1042.5",
        "due_date": "2026-11-30",
        "paid": False,
        "status": None,  # the default only applies when the document is first typed
    }
    with db.tenant_session(tenant) as session:
        stored = session.scalars(
            select(IndexValue.value_number).where(IndexValue.value_number.isnot(None))
        ).all()
    assert stored == [Decimal("1042.5")]

    listed = office["client"].get(f"{API}/folders/{office['folder']['id']}/children").json()
    assert listed["documents"]["items"][0]["document_type_id"] == made["id"]
    assert len(audit_events(db, tenant, "document.indexed")) == 2


def test_every_wrong_value_is_reported_together(office: dict[str, Any]) -> None:
    made = make_type(office).json()
    response = put_index(
        office,
        made["id"],
        {
            "invoice_number": "bad",
            "total": "-5",
            "due_date": "tomorrow",
            "paid": "perhaps",
            "status": "pending",
            "nonsense": "x",
        },
    )
    assert response.status_code == 422
    problems = {item["field"]: item["message"] for item in response.json()["error"]["fields"]}
    assert set(problems) == {"invoice_number", "total", "due_date", "paid", "status", "nonsense"}
    assert "format" in problems["invoice_number"]
    assert "0 or more" in problems["total"]
    assert "no such field" in problems["nonsense"]
    after = office["client"].get(office["index"]).json()
    assert after["document_type_id"] is None  # nothing was saved


def test_fields_left_out_are_cleared(office: dict[str, Any]) -> None:
    made = make_type(office).json()
    put_index(office, made["id"], GOOD)
    again = put_index(office, made["id"], {"invoice_number": "INV-2000", "total": 5}).json()
    values = {f["key"]: f["value"] for f in again["fields"]}
    assert values["invoice_number"] == "INV-2000"
    assert values["total"] == "5"
    assert values["due_date"] is None
    assert values["paid"] is None


def test_removing_the_type_removes_the_values(office: dict[str, Any]) -> None:
    made = make_type(office).json()
    put_index(office, made["id"], GOOD)
    cleared = put_index(office, None, {}).json()
    assert (cleared["document_type_id"], cleared["fields"]) == (None, [])
    assert office["client"].get(office["index"]).json()["fields"] == []


def test_a_document_keeps_its_version_until_it_is_saved_again(office: dict[str, Any]) -> None:
    client, csrf = office["client"], office["csrf"]
    made = make_type(office).json()
    put_index(office, made["id"], GOOD)
    fields = [field for field in INVOICE_FIELDS if field["key"] != "paid"] + [
        {"key": "po", "label": "PO number", "data_type": "text"}
    ]
    client.post(
        f"{API}/document-types/{made['id']}/versions",
        json={"row_version": made["row_version"], "fields": fields},
        headers=csrf,
    )

    stale = client.get(office["index"]).json()
    assert (stale["schema_version"], stale["current_version"], stale["outdated"]) == (1, 2, True)
    assert [f["key"] for f in stale["fields"]] == [f["key"] for f in fields]  # laid out on v2
    carried = {f["key"]: f["value"] for f in stale["fields"]}
    assert carried["invoice_number"] == "INV-1042"  # the old value shows on the new field
    assert carried["po"] is None

    without_paid = {key: value for key, value in GOOD.items() if key != "paid"}
    saved = put_index(office, made["id"], {**without_paid, "po": "PO-77"}).json()
    assert (saved["schema_version"], saved["outdated"]) == (2, False)
    assert "paid" not in {f["key"] for f in saved["fields"]}


def test_defaults_apply_only_when_a_document_is_first_typed(office: dict[str, Any]) -> None:
    made = make_type(office).json()
    first = put_index(office, made["id"], GOOD).json()
    assert {f["key"]: f["value"] for f in first["fields"]}["status"] == "open"
    second = put_index(office, made["id"], GOOD).json()
    assert {f["key"]: f["value"] for f in second["fields"]}["status"] is None


def test_read_only_fields_cannot_be_typed_in(office: dict[str, Any]) -> None:
    fields = [
        {"key": "ref", "label": "Reference", "data_type": "text"},
        {"key": "score", "label": "Score", "data_type": "number", "read_only": True},
    ]
    made = make_type(office, "Scored", fields).json()
    refused = put_index(office, made["id"], {"ref": "x", "score": 9})
    assert refused.status_code == 422
    assert "cannot be changed" in refused.json()["error"]["fields"][0]["message"]
    assert put_index(office, made["id"], {"ref": "x"}).status_code == 200


def test_an_archived_type_cannot_be_given_to_more_documents(office: dict[str, Any]) -> None:
    client, csrf = office["client"], office["csrf"]
    made = make_type(office).json()
    put_index(office, made["id"], GOOD)
    client.patch(
        f"{API}/document-types/{made['id']}",
        json={"row_version": made["row_version"], "status": "archived"},
        headers=csrf,
    )
    other = put(client, csrf, office["folder"]["id"], "second.pdf", PDF + b"2").json()["document"]
    refused = client.put(
        f"{API}/documents/{other['id']}/index",
        json={"document_type_id": made["id"], "values": GOOD},
        headers=csrf,
    )
    assert refused.status_code == 409
    assert "archived" in refused.json()["error"]["message"]
    # The document that already had it can still be edited.
    assert put_index(office, made["id"], GOOD).status_code == 200


def test_a_stale_save_is_refused(office: dict[str, Any]) -> None:
    made = make_type(office).json()
    current = put_index(office, made["id"], GOOD).json()
    put_index(office, made["id"], {**GOOD, "total": 7})  # someone else saves first
    stale = office["client"].put(
        office["index"],
        json={
            "document_type_id": made["id"],
            "values": GOOD,
            "row_version": current["row_version"],
        },
        headers=office["csrf"],
    )
    assert stale.status_code == 409


def test_rights_decide_who_can_see_and_change_the_values(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    made = make_type(office).json()
    put_index(office, made["id"], GOOD)
    anchor = uuid.UUID(office["folder"]["acl_anchor_id"])

    reader = new_user(db, tenant, "reader")
    allow(db, tenant, anchor, reader, "read")
    client, csrf = sign_in(app, db, tenant, "reader")
    assert client.get(office["index"]).status_code == 200
    denied = client.put(
        office["index"], json={"document_type_id": made["id"], "values": GOOD}, headers=csrf
    )
    assert denied.status_code == 403
    allow(db, tenant, anchor, reader, "edit_metadata")
    assert (
        client.put(
            office["index"],
            json={"document_type_id": made["id"], "values": {**GOOD, "total": 3}},
            headers=csrf,
        ).status_code
        == 200
    )

    new_user(db, tenant, "stranger")
    stranger, stranger_csrf = sign_in(app, db, tenant, "stranger")
    assert stranger.get(office["index"]).status_code == 404
    assert (
        stranger.put(
            office["index"],
            json={"document_type_id": made["id"], "values": GOOD},
            headers=stranger_csrf,
        ).status_code
        == 404
    )


def test_an_archived_folder_accepts_no_new_values(office: dict[str, Any]) -> None:
    client, csrf, folder = office["client"], office["csrf"], office["folder"]
    made = make_type(office).json()
    client.patch(
        f"{API}/folders/{folder['id']}",
        json={"row_version": folder["row_version"], "status": "archived"},
        headers=csrf,
    )
    assert put_index(office, made["id"], GOOD).status_code == 409


def test_another_organization_sees_neither_types_nor_values(
    app: FastAPI, office: dict[str, Any], db: Database, other_tenant: uuid.UUID
) -> None:
    made = make_type(office).json()
    put_index(office, made["id"], GOOD)
    boss_of(db, other_tenant, "intruder")
    intruder, csrf = sign_in(app, db, other_tenant, "intruder")
    assert intruder.get(f"{API}/document-types").json() == []
    assert intruder.get(f"{API}/document-types/{made['id']}").status_code == 404
    assert intruder.get(office["index"]).status_code == 404
    assert (
        intruder.put(
            office["index"], json={"document_type_id": made["id"], "values": GOOD}, headers=csrf
        ).status_code
        == 404
    )
