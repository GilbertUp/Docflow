"""F07 end to end: text is read, a type and values are proposed, and a person has the last word."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from docuflow.modules.documents.extraction import JOB_TYPE
from docuflow.modules.indexing.models import ExtractionFeedback
from docuflow.modules.indexing.suggest import JOB_TYPE as FIELDS_JOB
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from docuflow.platform.jobs import HandlerRegistry, JobRunner
from docuflow.platform.models import ProcessingJob
from tests.documents.files import FakeEngine, text_pdf
from tests.documents.test_upload import make, put
from tests.identity.support import assign_role, audit_events, new_role
from tests.repository.support import boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db

TEXT = [
    "Supplier invoice from ABC Ltd for services.   Invoice no INV-1042   Total 1,042.50   Paid no"
]

FIELDS: list[dict[str, Any]] = [
    {
        "key": "invoice_number",
        "label": "Invoice number",
        "data_type": "text",
        "extraction": {"labels": ["Invoice no"], "pattern": r"INV-\d{4}"},
    },
    {
        "key": "total",
        "label": "Total",
        "data_type": "number",
        "extraction": {"labels": ["Total"]},
    },
    {"key": "note", "label": "Note", "data_type": "text"},
]


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    app = make_app(db, audit, keys, MemoryObjectStore(), max_upload_bytes=1024 * 1024)
    app.state.text._engine = FakeEngine()
    return app


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss = boss_of(db, tenant)
    role = new_role(db, tenant, "Type admin", ("document_types.manage",))
    assign_role(db, tenant, role, user_id=boss.user_id)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Invoices")
    return {"client": client, "csrf": csrf, "folder": folder}


def make_type(office: dict[str, Any], **extra: Any) -> dict[str, Any]:
    body = {
        "name": "Supplier invoice",
        "fields": FIELDS,
        "keywords": ["supplier", "invoice", "total"],
        **extra,
    }
    response = office["client"].post(f"{API}/document-types", json=body, headers=office["csrf"])
    assert response.status_code == 201, response.text
    return response.json()


def upload(office: dict[str, Any]) -> dict[str, Any]:
    response = put(
        office["client"], office["csrf"], office["folder"]["id"], "invoice.pdf", text_pdf(TEXT)
    )
    assert response.status_code == 201, response.text
    return response.json()["document"]


def run_jobs(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    """Reads text, then proposes values, the way the worker would."""
    registry = HandlerRegistry()
    registry.register(JOB_TYPE)(lambda job: app.state.text.run(db, job))
    registry.register(FIELDS_JOB)(lambda job: app.state.suggestions.run(db, job))
    runner = JobRunner(db, registry, app.state.text._audit, 0)
    for job_type in (JOB_TYPE, FIELDS_JOB):
        with db.tenant_session(tenant) as session:
            ids = list(
                session.scalars(
                    select(ProcessingJob.id).where(
                        ProcessingJob.type == job_type, ProcessingJob.status == "queued"
                    )
                )
            )
        for job_id in ids:
            runner.run(tenant, job_id)


def suggestions(office: dict[str, Any], document_id: str) -> dict[str, Any]:
    response = office["client"].get(f"{API}/documents/{document_id}/suggestions")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_confident_type_and_values_are_filled_in_with_where_they_came_from(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    made = make_type(office, auto_accept_threshold=0.5, review_threshold=0.2)
    document = upload(office)
    run_jobs(app, db, tenant)

    shown = suggestions(office, document["id"])
    assert shown["classification"]["type_id"] == made["id"]
    assert shown["classification"]["applied"] is True
    by_key = {item["key"]: item for item in shown["fields"]}
    assert set(by_key) == {"invoice_number", "total"}  # no rule, so no suggestion for "note"
    assert by_key["invoice_number"]["value"] == "INV-1042"
    assert by_key["total"]["value"] in {"1042.50", "1042.5"}
    assert by_key["invoice_number"]["page_number"] == 1
    assert by_key["invoice_number"]["state"] == "accepted"

    index = office["client"].get(f"{API}/documents/{document['id']}/index").json()
    values = {item["key"]: item["value"] for item in index["fields"]}
    assert values["invoice_number"] == "INV-1042"
    (event,) = audit_events(db, tenant, "document.fields_suggested")
    assert event.details["type_applied"] is True
    assert sorted(event.details["filled"]) == ["invoice_number", "total"]


def test_below_the_bar_nothing_is_filled_and_the_person_decides(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    made = make_type(
        office,
        keywords=["supplier", "invoice", "total", "receipt"],
        auto_accept_threshold=0.95,
        review_threshold=0.2,
    )
    document = upload(office)
    run_jobs(app, db, tenant)

    shown = suggestions(office, document["id"])
    assert shown["classification"]["applied"] is False
    index = office["client"].get(f"{API}/documents/{document['id']}/index").json()
    assert index["document_type_id"] is None

    # The person chooses the type and keeps one suggestion, correcting the other.
    saved = office["client"].put(
        f"{API}/documents/{document['id']}/index",
        json={
            "document_type_id": made["id"],
            "values": {"invoice_number": "INV-1042", "total": "999"},
        },
        headers=office["csrf"],
    )
    assert saved.status_code == 200, saved.text
    states = {item["key"]: item["state"] for item in suggestions(office, document["id"])["fields"]}
    assert states == {"invoice_number": "accepted", "total": "corrected"}
    with db.tenant_session(tenant) as session:
        (feedback,) = session.scalars(select(ExtractionFeedback)).all()
        assert feedback.after_value in {"999", "999.00"}


def test_a_value_a_person_already_set_is_never_overwritten(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    made = make_type(office, auto_accept_threshold=0.5, review_threshold=0.2)
    document = upload(office)
    office["client"].put(
        f"{API}/documents/{document['id']}/index",
        json={"document_type_id": made["id"], "values": {"invoice_number": "INV-9999"}},
        headers=office["csrf"],
    )
    run_jobs(app, db, tenant)
    index = office["client"].get(f"{API}/documents/{document['id']}/index").json()
    values = {item["key"]: item["value"] for item in index["fields"]}
    assert values["invoice_number"] == "INV-9999"
    assert values["total"] is not None  # the empty field was still filled


def test_running_again_needs_read_text_and_the_right_to_edit(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    make_type(office)
    document = upload(office)
    url = f"{API}/documents/{document['id']}/suggestions/run"
    early = office["client"].post(url, headers=office["csrf"])
    assert early.status_code == 409  # the text has not been read yet
    run_jobs(app, db, tenant)
    again = office["client"].post(url, headers=office["csrf"])
    assert again.status_code == 202
    assert again.json()["version_number"] == 1


def test_thresholds_and_keywords_are_checked(office: dict[str, Any]) -> None:
    bad = office["client"].post(
        f"{API}/document-types",
        json={
            "name": "Odd",
            "fields": FIELDS,
            "auto_accept_threshold": 0.4,
            "review_threshold": 0.8,
        },
        headers=office["csrf"],
    )
    assert bad.status_code == 400
    broken = [{**FIELDS[0], "extraction": {"pattern": "(a+)+"}}]
    refused = office["client"].post(
        f"{API}/document-types",
        json={"name": "Loop", "fields": broken},
        headers=office["csrf"],
    )
    assert refused.status_code == 422
