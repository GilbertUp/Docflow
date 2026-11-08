"""F08 over HTTP: finding documents, and never finding what a person may not see."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from docuflow.modules.documents.extraction import JOB_TYPE
from docuflow.modules.search.service import JOB_TYPE as SEARCH_JOB
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from docuflow.platform.jobs import HandlerRegistry, JobRunner
from docuflow.platform.models import ProcessingJob
from tests.documents.files import FakeEngine, text_pdf
from tests.documents.test_upload import make, put
from tests.identity.support import assign_role, audit_events, new_role, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db

SUPPLIER = "Invoice from Supplier ABC for consulting services, due on 30 November 2026."
OTHER = "Minutes of the board meeting held in Kigali about the annual budget and hiring."


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
    return {"client": client, "csrf": csrf, "folder": folder, "cabinet": cabinet}


def upload(office: dict[str, Any], name: str, text: str) -> dict[str, Any]:
    response = put(office["client"], office["csrf"], office["folder"]["id"], name, text_pdf([text]))
    assert response.status_code == 201, response.text
    return response.json()["document"]


def run_jobs(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    registry = HandlerRegistry()
    registry.register(JOB_TYPE)(lambda job: app.state.text.run(db, job))
    registry.register(SEARCH_JOB)(lambda job: app.state.search.run(db, job))
    runner = JobRunner(db, registry, app.state.text._audit, 0)
    for job_type in (SEARCH_JOB, JOB_TYPE, SEARCH_JOB):
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


def find(client: Any, q: str, **params: Any) -> dict[str, Any]:
    response = client.get(f"{API}/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_documents_are_found_by_title_and_by_the_words_inside(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    supplier = upload(office, "abc-invoice.pdf", SUPPLIER)
    upload(office, "board.pdf", OTHER)
    run_jobs(app, db, tenant)
    client = office["client"]

    by_text = find(client, "consulting")
    assert [hit["id"] for hit in by_text["hits"]] == [supplier["id"]]
    assert by_text["hits"][0]["matched_in"] == "text"
    assert "[[consulting]]" in by_text["hits"][0]["snippet"]

    by_title = find(client, "board")
    assert "board.pdf" in {hit["title"] for hit in by_title["hits"]}
    assert find(client, "nothingmatchesthis")["total"] == 0


def test_queries_support_phrases_alternatives_and_exclusion(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    upload(office, "a.pdf", SUPPLIER)
    upload(office, "b.pdf", OTHER)
    run_jobs(app, db, tenant)
    client = office["client"]
    assert find(client, '"board meeting"')["total"] == 1
    assert find(client, "consulting or budget")["total"] == 2
    assert find(client, "invoice -consulting")["total"] == 0
    assert find(client, "november supplier")["total"] == 1  # every word must be present


def test_a_person_only_finds_what_they_may_read_and_text_needs_preview(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    upload(office, "secret-plan.pdf", "The merger with Orion is confidential.")
    run_jobs(app, db, tenant)
    anchor = uuid.UUID(office["folder"]["acl_anchor_id"])

    stranger_id = new_user(db, tenant, "stranger")
    stranger, _ = sign_in(app, db, tenant, "stranger")
    assert find(stranger, "merger")["total"] == 0
    assert find(stranger, "secret")["total"] == 0

    allow(db, tenant, anchor, stranger_id, "read")
    # Read lets the title be found, but the words inside need the preview right.
    assert find(stranger, "merger")["total"] == 0
    titled = find(stranger, "secret")
    assert titled["total"] == 1
    assert titled["hits"][0]["snippet"] is None

    allow(db, tenant, anchor, stranger_id, "preview")
    seen = find(stranger, "merger")
    assert seen["total"] == 1
    assert "[[merger]]" in seen["hits"][0]["snippet"]


def test_saved_details_become_searchable_and_filters_narrow_results(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf = office["client"], office["csrf"]
    made = client.post(
        f"{API}/document-types",
        json={
            "name": "Supplier invoice",
            "fields": [{"key": "ref", "label": "Reference", "data_type": "text"}],
        },
        headers=csrf,
    )
    assert made.status_code == 201, made.text
    document = upload(office, "x.pdf", SUPPLIER)
    run_jobs(app, db, tenant)
    assert find(client, "zebra123")["total"] == 0
    saved = client.put(
        f"{API}/documents/{document['id']}/index",
        json={"document_type_id": made.json()["id"], "values": {"ref": "ZEBRA123"}},
        headers=csrf,
    )
    assert saved.status_code == 200, saved.text
    run_jobs(app, db, tenant)
    assert find(client, "zebra123")["hits"][0]["type_name"] == "Supplier invoice"
    assert find(client, "zebra123", type_id=made.json()["id"])["total"] == 1
    assert find(client, "zebra123", type_id=str(uuid.uuid4()))["total"] == 0
    assert find(client, "consulting", cabinet_id=str(uuid.uuid4()))["total"] == 0
    assert find(client, "consulting", cabinet_id=office["cabinet"]["id"])["total"] == 1


def test_paging_bad_queries_and_the_audit_trail(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    for number in range(3):
        upload(office, f"report-{number}.pdf", f"Quarterly report number {number} for review.")
    run_jobs(app, db, tenant)
    client = office["client"]
    first = find(client, "report", limit=2)
    second = find(client, "report", limit=2, offset=2)
    assert (first["total"], len(first["hits"]), len(second["hits"])) == (3, 2, 1)
    assert not {h["id"] for h in first["hits"]} & {h["id"] for h in second["hits"]}
    assert client.get(f"{API}/search", params={"q": "   "}).status_code == 400
    assert client.get(f"{API}/search", params={"q": "x" * 401}).status_code == 422
    assert client.get(f"{API}/search").status_code == 422
    events = audit_events(db, tenant, "search.performed")
    assert events[0].details["query"] == "report"
