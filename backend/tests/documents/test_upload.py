"""F03 upload and download over HTTP: storage, file type, size, duplicates and permissions."""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from docuflow.modules.documents.models import BinaryObject
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from tests.identity.support import audit_events, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n" + b"invoice supplier ABC " * 40 + b"\n%%EOF"
LIMIT = 64 * 1024


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider, store: MemoryObjectStore) -> FastAPI:
    return make_app(db, audit, keys, store, max_upload_bytes=LIMIT)


def make(client: TestClient, csrf: dict[str, str], path: str, name: str) -> dict:
    response = client.post(f"{API}{path}", json={"name": name}, headers=csrf)
    assert response.status_code == 201, response.text
    return response.json()


def put(
    client: TestClient,
    csrf: dict[str, str],
    folder_id: str,
    name: str,
    body: bytes,
    kind: str = "folders",
):
    return client.post(
        f"{API}/{kind}/{folder_id}/documents",
        params={"filename": name},
        content=body,
        headers={**csrf, "Content-Type": "application/octet-stream"},
    )


@pytest.fixture
def finance(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict:
    boss_of(db, tenant)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Supplier Invoices")
    return {"client": client, "csrf": csrf, "cabinet": cabinet, "folder": folder}


def test_upload_then_download_returns_the_same_bytes(finance: dict, db: Database, tenant) -> None:
    client, csrf, folder = finance["client"], finance["csrf"], finance["folder"]
    response = put(client, csrf, folder["id"], "ABC invoice.pdf", PDF)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["document"]["title"] == "ABC invoice.pdf"
    assert result["document"]["size_bytes"] == len(PDF)
    assert result["version"]["number"] == 1
    assert result["version"]["state"] == "released"
    assert result["duplicate"] is False

    listing = client.get(f"{API}/folders/{folder['id']}/children").json()
    assert [d["title"] for d in listing["documents"]["items"]] == ["ABC invoice.pdf"]

    download = client.get(f"{API}/documents/{result['document']['id']}/download")
    assert download.status_code == 200
    assert download.content == PDF
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["content-disposition"].startswith(
        'attachment; filename="ABC invoice.pdf"'
    )

    versions = client.get(f"{API}/documents/{result['document']['id']}/versions").json()
    assert [v["number"] for v in versions] == [1]
    assert len(audit_events(db, tenant, "document.uploaded")) == 1
    assert len(audit_events(db, tenant, "document.downloaded")) == 1


def test_uploading_straight_into_a_cabinet_works(finance: dict) -> None:
    response = put(
        finance["client"], finance["csrf"], finance["cabinet"]["id"], "root.pdf", PDF, "cabinets"
    )
    assert response.status_code == 201
    assert response.json()["document"]["folder_id"] is None


def test_stored_bytes_are_encrypted_and_keys_hide_the_filename(
    finance: dict, store: MemoryObjectStore
) -> None:
    put(finance["client"], finance["csrf"], finance["folder"]["id"], "secret-name.pdf", PDF)
    assert len(store.objects) == 1
    (key, blob), *_ = store.objects.items()
    assert b"supplier ABC" not in blob
    assert "secret-name" not in key


def test_the_type_comes_from_the_bytes_not_the_name(
    finance: dict, store: MemoryObjectStore
) -> None:
    client, csrf, folder = finance["client"], finance["csrf"], finance["folder"]
    program = put(client, csrf, folder["id"], "invoice.pdf", b"MZ\x90\x00 not a pdf at all")
    assert program.status_code == 415
    assert program.json()["error"]["code"] == "unsupported_type"
    assert store.objects == {}
    assert client.get(f"{API}/folders/{folder['id']}/children").json()["documents"]["total"] == 0

    text = put(client, csrf, folder["id"], "notes.txt", "Café notes\n".encode())
    assert text.status_code == 201
    binary_text = put(client, csrf, folder["id"], "notes2.txt", b"abc\x00def")
    assert binary_text.status_code == 415

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("[Content_Types].xml", "<Types/>")
    assert put(client, csrf, folder["id"], "letter.docx", archive.getvalue()).status_code == 201
    assert put(client, csrf, folder["id"], "letter.zip", archive.getvalue()).status_code == 415


def test_empty_and_oversized_files_are_refused_and_leave_nothing(
    finance: dict, store: MemoryObjectStore
) -> None:
    client, csrf, folder = finance["client"], finance["csrf"], finance["folder"]
    assert put(client, csrf, folder["id"], "empty.pdf", b"").status_code == 400
    big = PDF + b"x" * LIMIT
    refused = put(client, csrf, folder["id"], "big.pdf", big)
    assert refused.status_code == 413
    assert refused.json()["error"]["code"] == "too_large"
    assert store.objects == {}
    assert client.get(f"{API}/folders/{folder['id']}/children").json()["documents"]["total"] == 0


def test_a_path_in_the_filename_is_reduced_to_the_name(finance: dict) -> None:
    response = put(
        finance["client"], finance["csrf"], finance["folder"]["id"], "..\\..\\etc/evil.pdf", PDF
    )
    assert response.json()["document"]["title"] == "evil.pdf"


def test_a_duplicate_shares_one_stored_object(
    finance: dict, db: Database, tenant: uuid.UUID, store: MemoryObjectStore
) -> None:
    client, csrf, folder = finance["client"], finance["csrf"], finance["folder"]
    first = put(client, csrf, folder["id"], "a.pdf", PDF).json()
    second = put(client, csrf, folder["id"], "b.pdf", PDF).json()
    assert second["duplicate"] is True
    assert second["duplicate_of"] == first["document"]["id"]
    assert second["document"]["id"] != first["document"]["id"]
    assert len(store.objects) == 1
    with db.tenant_session(tenant) as session:
        assert [row.ref_count for row in session.scalars(select(BinaryObject))] == [2]
    for document in (first, second):
        got = client.get(f"{API}/documents/{document['document']['id']}/download")
        assert got.content == PDF


def test_a_duplicate_does_not_reveal_a_document_the_caller_cannot_read(
    app: FastAPI, finance: dict, db: Database, tenant: uuid.UUID
) -> None:
    put(finance["client"], finance["csrf"], finance["folder"]["id"], "hr-secret.pdf", PDF)
    boss_of(db, tenant, "other")
    other, other_csrf = sign_in(app, db, tenant, "other")
    mine = make(other, other_csrf, "/cabinets", "Mine")
    result = put(other, other_csrf, mine["id"], "same.pdf", PDF, "cabinets").json()
    assert result["duplicate"] is True
    assert result["duplicate_of"] is None


def test_permissions_gate_upload_and_download(
    app: FastAPI, finance: dict, db: Database, tenant: uuid.UUID, store: MemoryObjectStore
) -> None:
    uploaded = put(finance["client"], finance["csrf"], finance["folder"]["id"], "a.pdf", PDF).json()
    anchor = uuid.UUID(finance["folder"]["acl_anchor_id"])
    reader = new_user(db, tenant, "reader")
    allow(db, tenant, anchor, reader, "read")
    client, csrf = sign_in(app, db, tenant, "reader")

    before = len(store.objects)
    denied = put(client, csrf, finance["folder"]["id"], "b.pdf", PDF + b"more")
    assert denied.status_code == 403
    assert len(store.objects) == before

    no_download = client.get(f"{API}/documents/{uploaded['document']['id']}/download")
    assert no_download.status_code == 403
    assert no_download.json()["error"]["code"] == "forbidden"

    allow(db, tenant, anchor, reader, "download")
    assert client.get(f"{API}/documents/{uploaded['document']['id']}/download").content == PDF

    stranger = new_user(db, tenant, "stranger")
    assert stranger
    outsider, _ = sign_in(app, db, tenant, "stranger")
    hidden = outsider.get(f"{API}/documents/{uploaded['document']['id']}/download")
    assert hidden.status_code == 404
    assert outsider.get(f"{API}/documents/{uploaded['document']['id']}/versions").status_code == 404


def test_another_organization_cannot_reach_a_document(
    app: FastAPI, finance: dict, db: Database, other_tenant: uuid.UUID
) -> None:
    uploaded = put(finance["client"], finance["csrf"], finance["folder"]["id"], "a.pdf", PDF).json()
    boss_of(db, other_tenant, "intruder")
    intruder, _ = sign_in(app, db, other_tenant, "intruder")
    assert intruder.get(f"{API}/documents/{uploaded['document']['id']}/download").status_code == 404


def test_a_failed_filing_removes_the_stored_object(
    app: FastAPI, finance: dict, store: MemoryObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(app.state.repo, "file_document", broken)
    client = TestClient(finance["client"].app, raise_server_exceptions=False)
    client.cookies.update(finance["client"].cookies)
    response = put(client, finance["csrf"], finance["folder"]["id"], "a.pdf", PDF)
    assert response.status_code == 500
    assert store.objects == {}
