"""F15 over HTTP: retention, legal holds, and disposal that destroys what it says it destroys."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update

from docuflow.modules.collaboration.models import Comment, Task
from docuflow.modules.documents.extraction import JOB_TYPE
from docuflow.modules.documents.models import BinaryObject, DocumentVersion, OcrResult, Page
from docuflow.modules.forms.models import FormSubmission
from docuflow.modules.indexing.models import ExtractedField, IndexValue
from docuflow.modules.indexing.suggest import JOB_TYPE as FIELDS_JOB
from docuflow.modules.repository.models import Document
from docuflow.modules.retention.models import DisposalEvent
from docuflow.modules.search.models import SearchEntry
from docuflow.modules.search.service import JOB_TYPE as SEARCH_JOB
from docuflow.modules.signatures.models import Approval, Signature, StampApplication
from docuflow.modules.workflow.models import WorkflowInstance
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database, utcnow
from docuflow.platform.jobs import HandlerRegistry, JobRunner
from docuflow.platform.models import ProcessingJob
from tests.documents.files import FakeEngine, text_pdf
from tests.documents.test_upload import make, put
from tests.documents.test_versions import add
from tests.identity.support import STRONG, assign_role, audit_events, new_role, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db

INVOICE = "TAX INVOICE   Supplier ABC   Invoice no INV-1042   Due 2026-11-30   Total 1,042.50"


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider, store: MemoryObjectStore) -> FastAPI:
    app = make_app(db, audit, keys, store, max_upload_bytes=1024 * 1024)
    app.state.text._engine = FakeEngine()
    return app


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss = boss_of(db, tenant)
    role = new_role(
        db,
        tenant,
        "Records manager",
        ("retention.manage", "document_types.manage", "workflows.manage", "forms.manage"),
    )
    assign_role(db, tenant, role, user_id=boss.user_id)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Invoices")
    other = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Archive")
    # Owning a cabinet does not include approving disposal. That is granted on purpose.
    allow(db, tenant, uuid.UUID(cabinet["acl_anchor_id"]), boss.user_id, "dispose")
    return {
        "id": str(boss.user_id),
        "client": client,
        "csrf": csrf,
        "folder": folder,
        "other": other,
    }


def upload(office: dict[str, Any], name: str = "a.pdf", text: str = INVOICE, where: str = "folder"):
    response = put(office["client"], office["csrf"], office[where]["id"], name, text_pdf([text]))
    assert response.status_code == 201, response.text
    return response.json()["document"]


def make_class(office: dict[str, Any], name: str = "Invoices 7 years", days: int = 1, **extra: Any):
    made = office["client"].post(
        f"{API}/retention-classes",
        json={"name": name, "duration_days": days, **extra},
        headers=office["csrf"],
    )
    assert made.status_code == 201, made.text
    return made.json()


def assign(office: dict[str, Any], doc: str, class_id: str | None):
    return office["client"].put(
        f"{API}/documents/{doc}/retention", json={"class_id": class_id}, headers=office["csrf"]
    )


def age(db: Database, tenant: uuid.UUID, doc: str, days: int) -> None:
    with db.tenant_session(tenant) as session:
        session.execute(
            update(Document)
            .where(Document.id == uuid.UUID(doc))
            .values(created_at=utcnow() - timedelta(days=days))
        )


def dispose(office: dict[str, Any], doc: str, reason: str = "End of retention"):
    return office["client"].post(
        f"{API}/documents/{doc}/dispose", json={"reason": reason}, headers=office["csrf"]
    )


def make_hold(office: dict[str, Any], name: str = "Audit 2026"):
    made = office["client"].post(
        f"{API}/legal-holds",
        json={"name": name, "reason": "Tax audit"},
        headers=office["csrf"],
    )
    assert made.status_code == 201, made.text
    return made.json()


def run_jobs(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    registry = HandlerRegistry()
    registry.register(JOB_TYPE)(lambda job: app.state.text.run(db, job))
    registry.register(FIELDS_JOB)(lambda job: app.state.suggestions.run(db, job))
    registry.register(SEARCH_JOB)(lambda job: app.state.search.run(db, job))
    runner = JobRunner(db, registry, app.state.text._audit, 0)
    for job_type in (SEARCH_JOB, JOB_TYPE, FIELDS_JOB, SEARCH_JOB):
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


def count(db: Database, tenant: uuid.UUID, model: Any, where: Any = None) -> int:
    with db.tenant_session(tenant) as session:
        query = select(func.count()).select_from(model)
        if where is not None:
            query = query.where(where)
        return int(session.scalar(query) or 0)


# Who may set it


def test_only_a_records_manager_sets_classes_holds_and_assignments(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = upload(office)["id"]
    new_user(db, tenant, "clerk")
    clerk, csrf = sign_in(app, db, tenant, "clerk")
    refused = clerk.post(
        f"{API}/retention-classes", json={"name": "Mine", "duration_days": 5}, headers=csrf
    )
    assert refused.status_code == 403
    assert clerk.get(f"{API}/legal-holds").status_code == 403
    cls = make_class(office)
    assert clerk.get(f"{API}/retention-classes").status_code == 200
    assert (
        office["client"]
        .post(
            f"{API}/retention-classes",
            json={"name": "Invoices 7 years", "duration_days": 9},
            headers=office["csrf"],
        )
        .status_code
        == 409
    )
    assert assign(office, doc, cls["id"]).status_code == 200
    assert len(audit_events(db, tenant, "document.retention_set")) == 1


# The clock


def test_a_document_moves_from_retained_to_eligible_and_a_hold_overrides_it(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = upload(office)["id"]
    client = office["client"]
    url = f"{API}/documents/{doc}/retention"
    assert client.get(url).json()["status"] == "not_assigned"

    cls = make_class(office, days=30)
    retained = assign(office, doc, cls["id"]).json()
    assert retained["status"] == "retained"
    assert retained["eligible_at"] is not None

    age(db, tenant, doc, 31)
    assert client.get(url).json()["status"] == "eligible"

    hold = make_hold(office)
    added = client.post(
        f"{API}/legal-holds/{hold['id']}/documents",
        json={"document_id": doc},
        headers=office["csrf"],
    )
    assert added.status_code == 204
    held = client.get(url).json()
    assert held["status"] == "held"
    assert [h["name"] for h in held["holds"]] == ["Audit 2026"]

    released = client.post(
        f"{API}/legal-holds/{hold['id']}/release",
        json={"reason": "Audit closed"},
        headers=office["csrf"],
    )
    assert released.status_code == 200
    assert released.json()["status"] == "released"
    assert client.get(url).json()["status"] == "eligible"
    again = client.post(
        f"{API}/legal-holds/{hold['id']}/release", json={"reason": "Twice"}, headers=office["csrf"]
    )
    assert again.status_code == 409
    assert len(audit_events(db, tenant, "legal_hold.released")) == 1


def test_the_clock_can_start_from_the_newest_version(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = upload(office)["id"]
    cls = make_class(office, days=30, trigger="last_version")
    assign(office, doc, cls["id"])
    age(db, tenant, doc, 400)
    with db.tenant_session(tenant) as session:
        session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.document_id == uuid.UUID(doc))
            .values(created_at=utcnow() - timedelta(days=400))
        )
    assert office["client"].get(f"{API}/documents/{doc}/retention").json()["status"] == "eligible"
    add(
        office["client"],
        office["csrf"],
        f"{API}/documents/{doc}",
        "a.pdf",
        text_pdf(["A newer wording"]),
        "Edit",
    )
    # A new version restarts the clock.
    assert office["client"].get(f"{API}/documents/{doc}/retention").json()["status"] == "retained"


# Refusals


def test_disposal_is_refused_unless_every_condition_holds(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = upload(office)["id"]
    no_class = dispose(office, doc)
    assert no_class.status_code == 409
    assert "no retention class" in no_class.json()["error"]["message"]

    cls = make_class(office, days=30)
    assign(office, doc, cls["id"])
    early = dispose(office, doc)
    assert early.status_code == 409
    assert "must be kept until" in early.json()["error"]["message"]

    age(db, tenant, doc, 31)
    hold = make_hold(office)
    office["client"].post(
        f"{API}/legal-holds/{hold['id']}/documents",
        json={"document_id": doc},
        headers=office["csrf"],
    )
    held = dispose(office, doc)
    assert held.status_code == 409
    assert "Audit 2026" in held.json()["error"]["message"]
    assert count(db, tenant, Document) == 1
    assert count(db, tenant, DisposalEvent) == 0


def test_disposal_needs_the_dispose_right_on_the_document(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = upload(office)["id"]
    cls = make_class(office, days=1)
    assign(office, doc, cls["id"])
    age(db, tenant, doc, 5)
    reader_id = new_user(db, tenant, "reader")
    allow(db, tenant, uuid.UUID(office["folder"]["acl_anchor_id"]), reader_id, "read")
    reader, csrf = sign_in(app, db, tenant, "reader")
    assert reader.get(f"{API}/disposal/candidates").json() == []
    refused = reader.post(f"{API}/documents/{doc}/dispose", json={}, headers=csrf)
    assert refused.status_code == 403
    new_user(db, tenant, "stranger")
    stranger, scsrf = sign_in(app, db, tenant, "stranger")
    assert (
        stranger.post(f"{API}/documents/{doc}/dispose", json={}, headers=scsrf).status_code == 404
    )
    allow(db, tenant, uuid.UUID(office["folder"]["acl_anchor_id"]), reader_id, "dispose")
    assert [c["document_id"] for c in reader.get(f"{API}/disposal/candidates").json()] == [doc]


def test_candidates_leave_out_documents_that_are_not_due_or_are_held(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    due = upload(office, "due.pdf", "Due text")["id"]
    young = upload(office, "young.pdf", "Young text")["id"]
    held = upload(office, "held.pdf", "Held text")["id"]
    cls = make_class(office, days=30)
    for doc in (due, young, held):
        assign(office, doc, cls["id"])
    age(db, tenant, due, 40)
    age(db, tenant, held, 40)
    hold = make_hold(office)
    office["client"].post(
        f"{API}/legal-holds/{hold['id']}/documents",
        json={"document_id": held},
        headers=office["csrf"],
    )
    listed = office["client"].get(f"{API}/disposal/candidates").json()
    assert [c["document_id"] for c in listed] == [due]
    assert listed[0]["retention_class"] == "Invoices 7 years"


# Disposal


def test_disposal_destroys_everything_about_a_document_and_leaves_one_record(
    app: FastAPI,
    office: dict[str, Any],
    db: Database,
    tenant: uuid.UUID,
    store: MemoryObjectStore,
) -> None:
    client, csrf = office["client"], office["csrf"]
    doc_type = client.post(
        f"{API}/document-types",
        json={
            "name": "Supplier invoice",
            "keywords": ["invoice", "supplier"],
            "auto_accept_threshold": 0.5,
            "review_threshold": 0.2,
            "fields": [
                {
                    "key": "invoice_number",
                    "label": "Invoice number",
                    "data_type": "text",
                    "extraction": {"labels": ["Invoice no"], "pattern": r"INV-\d{4}"},
                }
            ],
        },
        headers=csrf,
    ).json()
    doc = upload(office)["id"]
    sibling = upload(office, "sibling.pdf", "A different document entirely")["id"]
    path = f"{API}/documents/{doc}"
    assert (
        add(
            client,
            csrf,
            path,
            "a.pdf",
            text_pdf(["SECOND TAX INVOICE   Supplier ABC   Invoice no INV-1042   Total 9.00"]),
            "Edit",
        ).status_code
        == 201
    )
    run_jobs(app, db, tenant)
    assert (
        client.put(
            f"{path}/index",
            json={"document_type_id": doc_type["id"], "values": {"invoice_number": "INV-1042"}},
            headers=csrf,
        ).status_code
        == 200
    )
    assert (
        client.post(f"{path}/comments", json={"body": "Looks fine"}, headers=csrf).status_code
        == 201
    )
    assert (
        client.post(
            f"{path}/tasks", json={"title": "Check", "assignee_id": office["id"]}, headers=csrf
        ).status_code
        == 201
    )
    stamp = client.post(
        f"{API}/stamps", json={"name": "Approved", "text": "APPROVED"}, headers=csrf
    ).json()
    client.post(
        f"{path}/stamps",
        json={"stamp_id": stamp["id"], "page": 1, "x": 0.5, "y": 0.5},
        headers=csrf,
    )
    assert (
        client.post(f"{path}/signatures", json={"password": STRONG}, headers=csrf).status_code
        == 201
    )
    flow = client.post(
        f"{API}/workflows",
        json={
            "name": "Approval",
            "steps": [{"name": "Check", "kind": "approval", "assignee_id": office["id"]}],
        },
        headers=csrf,
    ).json()
    started = client.post(f"{path}/workflows", json={"workflow_id": flow["id"]}, headers=csrf)
    step = started.json()["steps"][0]["id"]
    client.post(
        f"{API}/workflow-steps/{step}/decision",
        json={"decision": "approve", "comment": "ok"},
        headers=csrf,
    )
    client.post(
        f"{path}/links",
        json={"to_document_id": sibling, "relation_type": "related"},
        headers=csrf,
    )
    form = client.post(
        f"{API}/forms",
        json={"name": "Note", "fields": [{"key": "text", "label": "Text", "data_type": "text"}]},
        headers=csrf,
    ).json()
    client.post(
        f"{API}/forms/{form['id']}/submissions",
        json={"answers": {"text": "x"}, "document_id": doc},
        headers=csrf,
    )
    run_jobs(app, db, tenant)

    d = uuid.UUID(doc)
    for model, where in (
        (DocumentVersion, DocumentVersion.document_id == d),
        (Page, None),
        (OcrResult, None),
        (ExtractedField, None),
        (IndexValue, IndexValue.document_id == d),
        (Comment, Comment.document_id == d),
        (Task, Task.document_id == d),
        (Signature, Signature.document_id == d),
        (Approval, Approval.document_id == d),
        (StampApplication, StampApplication.document_id == d),
        (WorkflowInstance, WorkflowInstance.document_id == d),
        (FormSubmission, FormSubmission.document_id == d),
        (SearchEntry, SearchEntry.document_id == d),
    ):
        assert count(db, tenant, model, where) > 0, model.__name__  # there is something to destroy
    versions_before = count(db, tenant, DocumentVersion)
    objects_before = len(store.objects)

    cls = make_class(office, days=1)
    assign(office, doc, cls["id"])
    age(db, tenant, doc, 5)
    done = dispose(office, doc, "Retention ended")
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["title"], body["versions"], body["reason"]) == ("a.pdf", 2, "Retention ended")

    with db.tenant_session(tenant) as session:
        assert session.get(Document, d) is None
        assert session.get(Document, uuid.UUID(sibling)) is not None  # the other one is untouched
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentVersion)
                .where(DocumentVersion.document_id == d)
            )
            == 0
        )
    for model, where in (
        (Comment, Comment.document_id == d),
        (Task, Task.document_id == d),
        (Signature, Signature.document_id == d),
        (Approval, Approval.document_id == d),
        (StampApplication, StampApplication.document_id == d),
        (WorkflowInstance, WorkflowInstance.document_id == d),
        (FormSubmission, FormSubmission.document_id == d),
        (SearchEntry, SearchEntry.document_id == d),
        (IndexValue, IndexValue.document_id == d),
    ):
        assert count(db, tenant, model, where) == 0, model.__name__
    assert count(db, tenant, DocumentVersion) == versions_before - 2
    assert len(store.objects) == objects_before - 2  # both stored files are gone

    # One permanent record, with the hash of every file that was destroyed.
    events = client.get(f"{API}/disposal/events").json()
    assert [(e["title"], e["versions"]) for e in events] == [("a.pdf", 2)]
    with db.tenant_session(tenant) as session:
        event = session.scalars(select(DisposalEvent)).one()
        assert len(event.version_hashes) == 2
        assert all(len(item["sha256"]) == 64 for item in event.version_hashes)
    assert len(audit_events(db, tenant, "document.disposed")) == 1
    assert client.get(f"{path}/retention").status_code == 404
    assert dispose(office, doc).status_code == 404


def test_a_file_shared_with_another_document_is_kept(
    app: FastAPI,
    office: dict[str, Any],
    db: Database,
    tenant: uuid.UUID,
    store: MemoryObjectStore,
) -> None:
    first = upload(office, "one.pdf", "Identical content")["id"]
    second = upload(office, "two.pdf", "Identical content", where="other")["id"]
    with db.tenant_session(tenant) as session:
        assert session.scalar(select(func.count()).select_from(BinaryObject)) == 1
    cls = make_class(office, days=1)
    assign(office, first, cls["id"])
    age(db, tenant, first, 5)
    assert dispose(office, first).status_code == 200
    assert len(store.objects) == 1  # still needed by the other document
    kept = office["client"].get(f"{API}/documents/{second}/download")
    assert kept.status_code == 200
    assert kept.content.startswith(b"%PDF")
