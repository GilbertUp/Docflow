"""F04 over HTTP: a version is queued for reading when filed, and its text is read back."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from docuflow.modules.documents.extraction import JOB_TYPE
from docuflow.modules.documents.models import OcrResult, Page
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from docuflow.platform.jobs import HandlerRegistry, JobRunner
from docuflow.platform.models import ProcessingJob
from tests.documents.files import FakeEngine, mark_page, pdf_of_images, png_of, text_pdf
from tests.documents.test_upload import make, put
from tests.documents.test_versions import add
from tests.identity.support import audit_events, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db

LONG = "Invoice 1042 from Supplier ABC for consulting services, due on 30 November 2026."
DOCX = b"PK\x03\x04 pretend word document"


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider, engine: FakeEngine) -> FastAPI:
    app = make_app(db, audit, keys, MemoryObjectStore(), max_upload_bytes=2 * 1024 * 1024)
    app.state.text._engine = engine
    return app


@pytest.fixture
def folder(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss_of(db, tenant)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    made = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Scans")
    return {"client": client, "csrf": csrf, "folder": made}


def run_text_jobs(app: FastAPI, db: Database, tenant: uuid.UUID) -> int:
    """Runs every queued reading job the way the worker would."""
    registry = HandlerRegistry()
    registry.register(JOB_TYPE)(lambda job: app.state.text.run(db, job))
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
    return len(ids)


def upload(ctx: dict[str, Any], name: str, body: bytes) -> dict[str, Any]:
    response = put(ctx["client"], ctx["csrf"], ctx["folder"]["id"], name, body)
    assert response.status_code == 201, response.text
    return response.json()["document"]


def text_of(client: TestClient, document_id: str) -> dict[str, Any]:
    response = client.get(f"{API}/documents/{document_id}/text")
    assert response.status_code == 200, response.text
    return response.json()


def state_of(client: TestClient, document_id: str) -> str:
    return str(client.get(f"{API}/documents/{document_id}/versions").json()[-1]["text_state"])


def test_a_pdf_with_text_is_read_when_the_job_runs(
    app: FastAPI, folder: dict[str, Any], db: Database, tenant: uuid.UUID, engine: FakeEngine
) -> None:
    client = folder["client"]
    document = upload(folder, "invoice.pdf", text_pdf([LONG, "Page two " + LONG]))
    assert state_of(client, document["id"]) == "pending"
    assert text_of(client, document["id"])["pages"] == []  # nothing read yet

    assert run_text_jobs(app, db, tenant) == 1
    assert state_of(client, document["id"]) == "done"
    result = text_of(client, document["id"])
    assert (result["version"], result["state"], result["total_pages"]) == (1, "done", 2)
    assert [page["source"] for page in result["pages"]] == ["native", "native"]
    assert "Supplier ABC" in result["pages"][0]["text"]
    assert result["pages"][1]["text"].startswith("Page two")
    assert engine.calls == []

    (done,) = audit_events(db, tenant, "document.text_extracted")
    assert done.details["pages"] == 2
    assert done.details["recognised_pages"] == 0
    assert len(audit_events(db, tenant, "document.text_viewed")) == 2


def test_a_scanned_file_is_recognised_in_the_default_languages(
    app: FastAPI, folder: dict[str, Any], db: Database, tenant: uuid.UUID, engine: FakeEngine
) -> None:
    client = folder["client"]
    document = upload(folder, "scan.pdf", pdf_of_images([mark_page(), mark_page()]))
    run_text_jobs(app, db, tenant)
    pages = text_of(client, document["id"])["pages"]
    assert [page["source"] for page in pages] == ["fake", "fake"]
    assert [page["language"] for page in pages] == ["eng+fra", "eng+fra"]
    assert pages[0]["confidence"] == pytest.approx(0.9)
    assert [call[1] for call in engine.calls] == [["eng", "fra"], ["eng", "fra"]]


def test_reading_again_in_other_languages_makes_the_newest_result_current(
    app: FastAPI, folder: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf = folder["client"], folder["csrf"]
    document = upload(folder, "scan.png", png_of(mark_page()))
    run_text_jobs(app, db, tenant)
    assert text_of(client, document["id"])["pages"][0]["language"] == "eng+fra"

    queued = client.post(
        f"{API}/documents/{document['id']}/text/reprocess",
        params={"languages": "deu, fra"},
        headers=csrf,
    )
    assert queued.status_code == 202, queued.text
    assert queued.json() == {"version": 1, "state": "pending", "languages": ["deu", "fra"]}
    run_text_jobs(app, db, tenant)

    page = text_of(client, document["id"])["pages"][0]
    assert page["language"] == "deu+fra"
    assert "deu+fra" in page["text"]
    with db.tenant_session(tenant) as session:
        assert session.scalar(select(func.count()).select_from(OcrResult)) == 2
        assert session.scalar(select(func.count()).select_from(Page)) == 1


def test_a_repeated_delivery_of_the_job_adds_nothing(
    app: FastAPI, folder: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    document = upload(folder, "scan.png", png_of(mark_page()))
    run_text_jobs(app, db, tenant)
    run_text_jobs(app, db, tenant)
    with db.tenant_session(tenant) as session:
        assert session.scalar(select(func.count()).select_from(OcrResult)) == 1
    assert state_of(folder["client"], document["id"]) == "done"


@pytest.mark.parametrize(
    ("languages", "message"),
    [
        ("xxx", "not installed"),
        ("eng,fra,deu,spa,ita,por", "at most 5"),
        ("english", "not a language code"),
        ("eng;rm", "not a language code"),
    ],
)
def test_bad_language_choices_are_refused_with_a_reason(
    folder: dict[str, Any], languages: str, message: str
) -> None:
    document = upload(folder, "scan.png", png_of(mark_page()))
    response = folder["client"].post(
        f"{API}/documents/{document['id']}/text/reprocess",
        params={"languages": languages},
        headers=folder["csrf"],
    )
    assert response.status_code == 400
    assert message in response.json()["error"]["message"]


def test_the_available_languages_come_from_the_engine(
    folder: dict[str, Any], engine: FakeEngine
) -> None:
    engine.installed = ["eng", "swa", "chi_sim"]
    info = folder["client"].get(f"{API}/ocr/languages").json()
    assert info["available"] is True
    assert info["languages"] == ["eng", "swa", "chi_sim"]
    assert info["defaults"] == ["eng", "fra"]
    assert info["max_per_run"] == 5
    assert info["engine_version"] == "fake 1.0"


def test_without_an_engine_text_files_still_work_and_scans_fail_with_a_reason(
    app: FastAPI, folder: dict[str, Any], db: Database, tenant: uuid.UUID, engine: FakeEngine
) -> None:
    engine.is_available = False
    client = folder["client"]
    plain = upload(folder, "notes.txt", b"plain notes about the invoice and the supplier")
    scan = upload(folder, "scan.png", png_of(mark_page()))
    run_text_jobs(app, db, tenant)
    assert state_of(client, plain["id"]) == "done"
    assert text_of(client, plain["id"])["pages"][0]["text"].startswith("plain notes")
    assert state_of(client, scan["id"]) == "failed"
    assert "not set up" in text_of(client, scan["id"])["error"]

    assert client.get(f"{API}/ocr/languages").json()["available"] is False
    refused = client.post(f"{API}/documents/{scan['id']}/text/reprocess", headers=folder["csrf"])
    assert refused.status_code == 503


def test_a_damaged_pdf_ends_as_failed_with_a_message_and_blocks_nothing(
    app: FastAPI, folder: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client = folder["client"]
    broken = upload(folder, "broken.pdf", b"%PDF-1.7 this is not really a pdf")
    run_text_jobs(app, db, tenant)
    assert state_of(client, broken["id"]) == "failed"
    result = text_of(client, broken["id"])
    assert "damaged" in result["error"]
    assert client.get(f"{API}/documents/{broken['id']}/download").status_code == 200
    assert len(audit_events(db, tenant, "document.text_failed")) == 1


def test_file_types_with_nothing_to_read_yet_are_marked_unsupported(
    app: FastAPI, folder: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    import io
    import zipfile

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("[Content_Types].xml", "<Types/>")
    document = upload(folder, "letter.docx", archive.getvalue())
    assert state_of(folder["client"], document["id"]) == "unsupported"
    assert run_text_jobs(app, db, tenant) == 0
    refused = folder["client"].post(
        f"{API}/documents/{document['id']}/text/reprocess", headers=folder["csrf"]
    )
    assert refused.status_code == 409


def test_a_file_that_is_still_being_read_cannot_be_queued_again(
    folder: dict[str, Any],
) -> None:
    document = upload(folder, "scan.png", png_of(mark_page()))
    refused = folder["client"].post(
        f"{API}/documents/{document['id']}/text/reprocess", headers=folder["csrf"]
    )
    assert refused.status_code == 409
    assert "already being read" in refused.json()["error"]["message"]


def test_a_new_version_is_read_and_its_text_replaces_the_old_view(
    app: FastAPI, folder: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf = folder["client"], folder["csrf"]
    document = upload(folder, "contract.pdf", text_pdf(["First version of the supplier contract."]))
    run_text_jobs(app, db, tenant)
    added = add(
        client,
        csrf,
        f"{API}/documents/{document['id']}",
        "contract v2.pdf",
        text_pdf(["Second version of the supplier contract, now with new payment terms."]),
    )
    assert added.status_code == 201
    run_text_jobs(app, db, tenant)
    result = text_of(client, document["id"])
    assert result["version"] == 2
    assert "Second version" in result["pages"][0]["text"]


def test_reading_the_text_needs_the_preview_right_and_hides_unreadable_documents(
    app: FastAPI, folder: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    document = upload(folder, "invoice.pdf", text_pdf([LONG]))
    run_text_jobs(app, db, tenant)
    anchor = uuid.UUID(folder["folder"]["acl_anchor_id"])

    reader = new_user(db, tenant, "reader")
    allow(db, tenant, anchor, reader, "read")
    client, csrf = sign_in(app, db, tenant, "reader")
    denied = client.get(f"{API}/documents/{document['id']}/text")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    assert (
        client.post(f"{API}/documents/{document['id']}/text/reprocess", headers=csrf).status_code
        == 403
    )

    allow(db, tenant, anchor, reader, "preview")
    assert client.get(f"{API}/documents/{document['id']}/text").status_code == 200

    new_user(db, tenant, "stranger")
    stranger, stranger_csrf = sign_in(app, db, tenant, "stranger")
    assert stranger.get(f"{API}/documents/{document['id']}/text").status_code == 404
    assert (
        stranger.post(
            f"{API}/documents/{document['id']}/text/reprocess", headers=stranger_csrf
        ).status_code
        == 404
    )


def test_another_organization_cannot_read_the_text(
    app: FastAPI, folder: dict[str, Any], db: Database, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    document = upload(folder, "invoice.pdf", text_pdf([LONG]))
    run_text_jobs(app, db, tenant)
    boss_of(db, other_tenant, "intruder")
    intruder, _ = sign_in(app, db, other_tenant, "intruder")
    assert intruder.get(f"{API}/documents/{document['id']}/text").status_code == 404
