"""F12 over HTTP: building forms, filling them in, and who sees the answers."""

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
from tests.identity.support import assign_role, audit_events, new_role, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db

LEAVE_FIELDS: list[dict[str, Any]] = [
    {
        "key": "days",
        "label": "Days off",
        "data_type": "number",
        "required": True,
        "validation": {"min": 1, "max": 30},
    },
    {"key": "start", "label": "First day", "data_type": "date", "required": True},
    {
        "key": "kind",
        "label": "Kind",
        "data_type": "choice",
        "choices": [{"value": "annual", "label": "Annual"}, {"value": "sick", "label": "Sick"}],
    },
    {"key": "reason", "label": "Reason", "data_type": "text", "validation": {"max_length": 40}},
    {"key": "paid", "label": "Paid", "data_type": "boolean"},
]


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    return make_app(db, audit, keys, MemoryObjectStore(), max_upload_bytes=1024 * 1024)


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss = boss_of(db, tenant)
    role = new_role(db, tenant, "Forms admin", ("forms.manage",))
    assign_role(db, tenant, role, user_id=boss.user_id)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "HR")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Requests")
    document = put(client, csrf, folder["id"], "request.pdf", text_pdf(["A request"])).json()[
        "document"
    ]
    return {"client": client, "csrf": csrf, "folder": folder, "document": document}


def clerk(app: FastAPI, db: Database, tenant: uuid.UUID, name: str = "clerk") -> dict[str, Any]:
    new_user(db, tenant, name)
    client, csrf = sign_in(app, db, tenant, name)
    return {"client": client, "csrf": csrf}


def make_form(office: dict[str, Any], name: str = "Leave request", fields: Any = None):
    return office["client"].post(
        f"{API}/forms",
        json={"name": name, "fields": LEAVE_FIELDS if fields is None else fields},
        headers=office["csrf"],
    )


def submit(who: dict[str, Any], form_id: str, answers: dict[str, Any], **extra: Any):
    return who["client"].post(
        f"{API}/forms/{form_id}/submissions",
        json={"answers": answers, **extra},
        headers=who["csrf"],
    )


GOOD = {"days": "3", "start": "2026-12-01", "kind": "annual", "reason": "Family", "paid": "yes"}


def test_only_someone_who_manages_forms_can_make_them(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    other = clerk(app, db, tenant)
    refused = other["client"].post(
        f"{API}/forms",
        json={"name": "Mine", "fields": LEAVE_FIELDS},
        headers=other["csrf"],
    )
    assert refused.status_code == 403
    created = make_form(office)
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1
    assert [f["key"] for f in other["client"].get(f"{API}/forms").json()[0]["fields"]][:2] == [
        "days",
        "start",
    ]
    assert make_form(office).status_code == 409  # same name


def test_every_bad_field_is_reported_at_once(office: dict[str, Any]) -> None:
    bad = make_form(
        office,
        "Broken",
        [
            {"key": "Days", "label": "Days", "data_type": "number"},
            {"key": "ok", "label": "Fine", "data_type": "text"},
            {"key": "ok", "label": "Again", "data_type": "text"},
        ],
    )
    assert bad.status_code == 422
    assert {item["field"] for item in bad.json()["error"]["fields"]} == {"Days", "ok"}
    assert make_form(office, "Empty", []).status_code == 400


def test_a_good_submission_is_stored_in_each_fields_own_form(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    form = make_form(office).json()
    staff = clerk(app, db, tenant)
    done = submit(staff, form["id"], {**GOOD, "days": 3, "paid": True})
    assert done.status_code == 201, done.text
    body = done.json()
    assert body["data"] == {
        "days": "3",
        "start": "2026-12-01",
        "kind": "annual",
        "reason": "Family",
        "paid": "yes",
    }
    assert body["form_version"] == 1
    assert body["labels"]["days"] == "Days off"
    assert len(audit_events(db, tenant, "form.submitted")) == 1


def test_every_problem_with_a_submission_comes_back_at_once(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    form = make_form(office).json()
    staff = clerk(app, db, tenant)
    bad = submit(
        staff,
        form["id"],
        {"days": "99", "kind": "holiday", "reason": "x" * 41, "nonsense": "1"},
    )
    assert bad.status_code == 422
    problems = {item["field"]: item["message"] for item in bad.json()["error"]["fields"]}
    assert set(problems) == {"days", "start", "kind", "reason", "nonsense"}
    assert problems["start"] == "This is required."
    assert "30" in problems["days"]
    assert staff["client"].get(f"{API}/forms/{form['id']}/submissions").json() == []


def test_people_see_their_own_submissions_and_managers_see_all(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    form = make_form(office).json()
    alice = clerk(app, db, tenant, "alice")
    bob = clerk(app, db, tenant, "bob")
    mine = submit(alice, form["id"], GOOD).json()
    submit(bob, form["id"], {**GOOD, "days": "5"})

    assert len(alice["client"].get(f"{API}/forms/{form['id']}/submissions").json()) == 1
    assert len(office["client"].get(f"{API}/forms/{form['id']}/submissions").json()) == 2
    assert alice["client"].get(f"{API}/form-submissions/{mine['id']}").status_code == 200
    assert bob["client"].get(f"{API}/form-submissions/{mine['id']}").status_code == 404
    assert office["client"].get(f"{API}/form-submissions/{mine['id']}").status_code == 200


def test_a_new_version_keeps_earlier_submissions_as_they_were(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    form = make_form(office).json()
    staff = clerk(app, db, tenant)
    first = submit(staff, form["id"], GOOD).json()
    newer = office["client"].post(
        f"{API}/forms/{form['id']}/versions",
        json={"row_version": form["row_version"], "fields": LEAVE_FIELDS[:2]},
        headers=office["csrf"],
    )
    assert newer.status_code == 201, newer.text
    assert newer.json()["version"] == 2
    again = submit(staff, form["id"], {"days": "2", "start": "2026-12-05"})
    assert again.status_code == 201
    assert again.json()["form_version"] == 2
    old = staff["client"].get(f"{API}/form-submissions/{first['id']}").json()
    assert old["form_version"] == 1
    assert old["data"]["kind"] == "annual"
    same = office["client"].post(
        f"{API}/forms/{form['id']}/versions",
        json={"row_version": newer.json()["row_version"], "fields": LEAVE_FIELDS[:2]},
        headers=office["csrf"],
    )
    assert same.status_code == 409


def test_an_archived_form_cannot_be_filled_in(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    form = make_form(office).json()
    staff = clerk(app, db, tenant)
    archived = office["client"].patch(
        f"{API}/forms/{form['id']}",
        json={"row_version": form["row_version"], "status": "archived"},
        headers=office["csrf"],
    )
    assert archived.status_code == 200
    assert submit(staff, form["id"], GOOD).status_code == 409
    assert office["client"].get(f"{API}/forms").json() == []


def test_a_submission_can_be_attached_to_a_document(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    form = make_form(office).json()
    doc = office["document"]["id"]
    anchor = uuid.UUID(office["folder"]["acl_anchor_id"])
    reader_id = new_user(db, tenant, "reader")
    allow(db, tenant, anchor, reader_id, "read")
    reader, reader_csrf = sign_in(app, db, tenant, "reader")
    reader_ctx = {"client": reader, "csrf": reader_csrf}

    assert submit(reader_ctx, form["id"], GOOD, document_id=doc).status_code == 403
    allow(db, tenant, anchor, reader_id, "annotate")
    attached = submit(reader_ctx, form["id"], GOOD, document_id=doc)
    assert attached.status_code == 201, attached.text
    listed = office["client"].get(f"{API}/documents/{doc}/form-submissions").json()
    assert [item["submitter_name"] for item in listed] == ["reader"]
    assert len(audit_events(db, tenant, "document.form_submitted")) == 1

    stranger = clerk(app, db, tenant, "stranger")
    assert submit(stranger, form["id"], GOOD, document_id=doc).status_code == 404
    assert stranger["client"].get(f"{API}/documents/{doc}/form-submissions").status_code == 404
