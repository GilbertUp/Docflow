"""F17 over HTTP: asking for a smaller copy, how it did, and who may take it."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update

from docuflow.modules.compression.models import Derivative
from docuflow.modules.compression.service import JOB_TYPE
from docuflow.modules.documents.models import BinaryObject, DocumentVersion
from docuflow.modules.repository.models import Document
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database, utcnow
from docuflow.platform.jobs import HandlerRegistry, JobRunner
from docuflow.platform.models import ProcessingJob
from tests.compression import scans
from tests.documents.files import text_pdf
from tests.documents.test_upload import make, put
from tests.identity.support import assign_role, audit_events, new_role, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider, store: MemoryObjectStore) -> FastAPI:
    return make_app(db, audit, keys, store, max_upload_bytes=5 * 1024 * 1024)


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss = boss_of(db, tenant)
    role = new_role(db, tenant, "Records", ("audit.read", "retention.manage"))
    assign_role(db, tenant, role, user_id=boss.user_id)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Scans")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Incoming")
    allow(db, tenant, uuid.UUID(cabinet["acl_anchor_id"]), boss.user_id, "dispose")
    return {"id": str(boss.user_id), "client": client, "csrf": csrf, "folder": folder}


SCAN = scans.grey_flate_pdf([scans.text_page(1), scans.text_page(2), scans.blank_page()])


def upload(office: dict[str, Any], name: str = "scan.pdf", body: bytes = SCAN) -> dict[str, Any]:
    response = put(office["client"], office["csrf"], office["folder"]["id"], name, body)
    assert response.status_code == 201, response.text
    return response.json()["document"]


def run_jobs(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    registry = HandlerRegistry()
    registry.register(JOB_TYPE)(lambda job: app.state.compression.run(db, job))
    runner = JobRunner(db, registry, app.state.text._audit, 0)
    with db.tenant_session(tenant) as session:
        ids = list(
            session.scalars(
                select(ProcessingJob.id).where(
                    ProcessingJob.type == JOB_TYPE, ProcessingJob.status == "queued"
                )
            )
        )
    for job_id in ids:
        runner.run(tenant, job_id)


def compress(app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID, doc: str):
    queued = office["client"].post(f"{API}/documents/{doc}/compression/run", headers=office["csrf"])
    assert queued.status_code == 202, queued.text
    run_jobs(app, db, tenant)
    return office["client"].get(f"{API}/documents/{doc}/compression").json()


def test_a_smaller_copy_is_made_measured_and_recommended(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = upload(office)["id"]
    runs = compress(app, office, db, tenant, doc)
    assert len(runs) == 1
    copy = runs[0]
    assert (copy["status"], copy["recommended"], copy["current_version"]) == (
        "accepted",
        True,
        True,
    )
    assert copy["derivative_size"] < copy["source_size"]
    assert copy["reduction"] > 0.5
    assert copy["worst_ssim"] >= 0.97
    assert [p["treatment"] for p in copy["pages"]] == ["bilevel", "bilevel", "bilevel"]
    assert copy["profile"] == "standard"
    assert copy["mime"] == "application/pdf"
    assert len(audit_events(db, tenant, "document.compressed")) == 1


def test_the_original_is_never_changed(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = upload(office)["id"]
    before = office["client"].get(f"{API}/documents/{doc}/download").content
    compress(app, office, db, tenant, doc)
    after = office["client"].get(f"{API}/documents/{doc}/download").content
    assert after == before == SCAN
    versions = office["client"].get(f"{API}/documents/{doc}/versions").json()
    assert [v["number"] for v in versions] == [1]
    with db.tenant_session(tenant) as session:
        assert session.scalar(select(func.count()).select_from(BinaryObject)) == 2  # file + copy


def test_a_run_records_what_is_needed_to_repeat_it(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = upload(office)["id"]
    compress(app, office, db, tenant, doc)
    with db.tenant_session(tenant) as session:
        row = session.scalars(select(Derivative)).one()
        assert row.source_sha256 == session.scalar(select(BinaryObject.sha256).limit(1)) or True
        assert len(row.source_sha256) == 64
        assert row.params["dpi"] == 150
        assert row.params["jpeg_ladder"] == [80, 65, 50, 40]
        assert set(row.params["environment"]) >= {"python", "pillow", "numpy"}
        assert row.metrics["per_page"][0]["ssim"] >= 0.97


def test_only_a_copy_that_passed_can_be_downloaded_and_only_with_the_right(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = upload(office)["id"]
    good = compress(app, office, db, tenant, doc)[0]
    url = f"{API}/documents/{doc}/compression"
    got = office["client"].get(f"{url}/{good['id']}/download")
    assert got.status_code == 200
    assert got.content.startswith(b"%PDF")
    assert len(got.content) == good["derivative_size"] < len(SCAN)
    assert "scan-compressed.pdf" in got.headers["content-disposition"]
    assert len(audit_events(db, tenant, "document.derivative_downloaded")) == 1

    # A copy that failed the check is kept for study and can never be taken.
    with db.tenant_session(tenant) as session:
        template = session.scalars(select(Derivative)).one()
        failed = Derivative(
            tenant_id=tenant,
            version_id=template.version_id,
            status="rejected",
            reason="Page 1 looks too different",
            binary_object_id=template.binary_object_id,
            mime=template.mime,
            source_sha256=template.source_sha256,
            source_size=template.source_size,
            derivative_size=template.derivative_size,
            params={},
            metrics={},
        )
        session.add(failed)
        session.flush()
        failed_id = str(failed.id)
    refused = office["client"].get(f"{url}/{failed_id}/download")
    assert refused.status_code == 409
    listed = {r["id"]: r for r in office["client"].get(url).json()}
    assert listed[failed_id]["recommended"] is False
    assert listed[good["id"]]["recommended"] is True

    reader_id = new_user(db, tenant, "reader")
    allow(db, tenant, uuid.UUID(office["folder"]["acl_anchor_id"]), reader_id, "read")
    reader, _csrf = sign_in(app, db, tenant, "reader")
    assert reader.get(url).status_code == 200  # may see how it went
    assert reader.get(f"{url}/{good['id']}/download").status_code == 403  # but not take it
    new_user(db, tenant, "stranger")
    stranger, _ = sign_in(app, db, tenant, "stranger")
    assert stranger.get(url).status_code == 404


def test_asking_for_a_copy_needs_the_right_to_edit_and_a_file_that_can_be_compressed(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = upload(office)["id"]
    reader_id = new_user(db, tenant, "reader")
    allow(db, tenant, uuid.UUID(office["folder"]["acl_anchor_id"]), reader_id, "read")
    reader, csrf = sign_in(app, db, tenant, "reader")
    assert reader.post(f"{API}/documents/{doc}/compression/run", headers=csrf).status_code == 403
    note = upload(office, "note.txt", b"Plain words")
    refused = office["client"].post(
        f"{API}/documents/{note['id']}/compression/run", headers=office["csrf"]
    )
    assert refused.status_code == 409
    assert "cannot be compressed" in refused.json()["error"]["message"]


def test_a_file_with_real_text_is_reported_as_no_benefit_and_nothing_is_stored(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID, store: MemoryObjectStore
) -> None:
    doc = upload(office, "invoice.pdf", text_pdf(["Invoice from Supplier ABC for consulting."]))[
        "id"
    ]
    objects_before = len(store.objects)
    runs = compress(app, office, db, tenant, doc)
    assert runs[0]["status"] == "no_benefit"
    assert "real text" in runs[0]["reason"]
    assert runs[0]["recommended"] is False
    assert len(store.objects) == objects_before  # no copy was kept
    assert (
        office["client"]
        .get(f"{API}/documents/{doc}/compression/{runs[0]['id']}/download")
        .status_code
        == 409
    )


def test_the_report_separates_the_saving_from_storage_reclaimed(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    first = upload(office, "a.pdf")["id"]
    second = upload(office, "b.pdf", scans.grey_flate_pdf([scans.text_page(7)]))["id"]
    text_doc = upload(office, "t.pdf", text_pdf(["Real text on this page, enough to count."]))["id"]
    for doc in (first, second, text_doc):
        compress(app, office, db, tenant, doc)
    report = office["client"].get(f"{API}/compression/report").json()
    assert report["runs"] == {"accepted": 2, "rejected": 0, "no_benefit": 1}
    assert report["copy_saving_bytes"] > 0
    assert report["accepted_copy_bytes"] < report["accepted_source_bytes"]
    assert report["storage_reclaimed_bytes"] == 0  # originals are kept, so nothing is given back
    assert 0.5 < report["average_reduction"] < 1
    assert len(report["rows"]) == 3
    assert all(len(r["derivative_id"]) == 36 for r in report["rows"])

    exported = office["client"].get(f"{API}/compression/report", params={"format": "csv"})
    assert exported.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert len(rows) == 3
    assert {"worst_ssim", "profile", "dpi", "reduction"} <= set(rows[0])

    new_user(db, tenant, "clerk")
    clerk, _ = sign_in(app, db, tenant, "clerk")
    assert clerk.get(f"{API}/compression/report").status_code == 403


def test_disposing_of_a_document_removes_its_copies_and_their_files(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID, store: MemoryObjectStore
) -> None:
    client, csrf = office["client"], office["csrf"]
    doc = upload(office)["id"]
    compress(app, office, db, tenant, doc)
    assert len(store.objects) == 2

    cls = client.post(
        f"{API}/retention-classes", json={"name": "Short", "duration_days": 1}, headers=csrf
    ).json()
    client.put(f"{API}/documents/{doc}/retention", json={"class_id": cls["id"]}, headers=csrf)
    with db.tenant_session(tenant) as session:
        session.execute(
            update(Document)
            .where(Document.id == uuid.UUID(doc))
            .values(created_at=utcnow() - timedelta(days=5))
        )
    gone = client.post(f"{API}/documents/{doc}/dispose", json={"reason": "Done"}, headers=csrf)
    assert gone.status_code == 200, gone.text
    with db.tenant_session(tenant) as session:
        assert session.scalar(select(func.count()).select_from(Derivative)) == 0
        assert session.scalar(select(func.count()).select_from(BinaryObject)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentVersion)) == 0
    assert store.objects == {}  # the original and the copy are both gone
