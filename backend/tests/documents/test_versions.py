"""F03 new versions: history, numbering under concurrency, permissions and refusals."""

from __future__ import annotations

import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from docuflow.modules.documents.models import BinaryObject
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from tests.documents.test_upload import LIMIT, PDF, make, put
from tests.identity.support import audit_events, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db

V2 = PDF + b"\nrevised terms: net 30\n"
V3 = PDF + b"\nrevised terms: net 45\n"


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider, store: MemoryObjectStore) -> FastAPI:
    return make_app(db, audit, keys, store, max_upload_bytes=LIMIT)


@pytest.fixture
def filed(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss = boss_of(db, tenant)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Contracts")
    document = put(client, csrf, folder["id"], "contract.pdf", PDF).json()["document"]
    return {
        "boss": boss,
        "client": client,
        "csrf": csrf,
        "folder": folder,
        "document": document,
        "path": f"{API}/documents/{document['id']}",
    }


def add(
    client: TestClient,
    csrf: dict[str, str],
    path: str,
    name: str,
    body: bytes,
    reason: str = "",
):
    return client.post(
        f"{path}/versions",
        params={"filename": name, "reason": reason},
        content=body,
        headers={**csrf, "Content-Type": "application/octet-stream"},
    )


def test_a_new_version_becomes_current_and_the_old_one_stays_downloadable(
    filed: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf, path = filed["client"], filed["csrf"], filed["path"]
    before = client.get(path).json()
    response = add(client, csrf, path, "contract v2.pdf", V2, "Payment terms changed")
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["version"]["number"] == 2
    assert result["version"]["reason"] == "Payment terms changed"
    assert result["version"]["original_filename"] == "contract v2.pdf"
    assert result["version"]["size_bytes"] == len(V2)
    assert result["document"]["size_bytes"] == len(V2)
    assert result["document"]["title"] == "contract.pdf"  # the identity does not change
    assert result["document"]["row_version"] == before["row_version"] + 1

    assert client.get(f"{path}/download").content == V2
    assert client.get(f"{path}/versions/1/download").content == PDF
    assert client.get(f"{path}/versions/2/download").content == V2
    history = client.get(f"{path}/versions").json()
    assert [(v["number"], v["size_bytes"]) for v in history] == [(1, len(PDF)), (2, len(V2))]
    assert [e.details["number"] for e in audit_events(db, tenant, "document.version_added")] == [2]
    assert len(audit_events(db, tenant, "document.downloaded")) == 3


def test_a_file_identical_to_the_current_version_is_refused(
    filed: dict[str, Any], store: MemoryObjectStore
) -> None:
    client, csrf, path = filed["client"], filed["csrf"], filed["path"]
    refused = add(client, csrf, path, "same.pdf", PDF)
    assert refused.status_code == 409
    assert "identical" in refused.json()["error"]["message"]
    assert len(client.get(f"{path}/versions").json()) == 1
    assert len(store.objects) == 1


def test_going_back_to_an_older_file_adds_a_version_and_shares_its_object(
    filed: dict[str, Any], db: Database, tenant: uuid.UUID, store: MemoryObjectStore
) -> None:
    client, csrf, path = filed["client"], filed["csrf"], filed["path"]
    assert add(client, csrf, path, "v2.pdf", V2).status_code == 201
    back = add(client, csrf, path, "v1 again.pdf", PDF, "Revert")
    assert back.status_code == 201
    assert back.json()["version"]["number"] == 3
    assert back.json()["duplicate"] is True
    assert client.get(f"{path}/download").content == PDF
    assert len(store.objects) == 2
    with db.tenant_session(tenant) as session:
        counts = sorted(row.ref_count for row in session.scalars(select(BinaryObject)))
    assert counts == [1, 2]


def test_concurrent_new_versions_get_distinct_numbers(
    filed: dict[str, Any], app: FastAPI, db: Database
) -> None:
    docs = app.state.documents
    document_id = uuid.UUID(filed["document"]["id"])

    def worker(index: int) -> int:
        body = PDF + f"\nconcurrent edit {index}\n".encode()
        added = docs.add_version(
            db, filed["boss"], document_id, f"edit{index}.pdf", io.BytesIO(body), ""
        )
        return int(added.version.number)

    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = sorted(pool.map(worker, range(4)))
    assert numbers == [2, 3, 4, 5]
    history = filed["client"].get(f"{filed['path']}/versions").json()
    assert [v["number"] for v in history] == [1, 2, 3, 4, 5]


def test_unsupported_empty_and_oversized_files_leave_the_history_alone(
    filed: dict[str, Any], store: MemoryObjectStore
) -> None:
    client, csrf, path = filed["client"], filed["csrf"], filed["path"]
    assert add(client, csrf, path, "x.pdf", b"MZ not a pdf").status_code == 415
    assert add(client, csrf, path, "x.pdf", b"").status_code == 400
    assert add(client, csrf, path, "x.pdf", PDF + b"x" * LIMIT).status_code == 413
    assert len(client.get(f"{path}/versions").json()) == 1
    assert len(store.objects) == 1


def test_adding_a_version_needs_the_version_right(
    app: FastAPI,
    filed: dict[str, Any],
    db: Database,
    tenant: uuid.UUID,
    store: MemoryObjectStore,
) -> None:
    anchor = uuid.UUID(filed["folder"]["acl_anchor_id"])
    editor = new_user(db, tenant, "editor")
    allow(db, tenant, anchor, editor, "read", "download")
    client, csrf = sign_in(app, db, tenant, "editor")

    denied = add(client, csrf, filed["path"], "v2.pdf", V2)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    assert len(store.objects) == 1

    allow(db, tenant, anchor, editor, "version")
    assert add(client, csrf, filed["path"], "v2.pdf", V2).status_code == 201


def test_an_unreadable_document_cannot_be_versioned_or_have_old_files_fetched(
    app: FastAPI, filed: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    add(filed["client"], filed["csrf"], filed["path"], "v2.pdf", V2)
    new_user(db, tenant, "stranger")
    stranger, csrf = sign_in(app, db, tenant, "stranger")
    assert add(stranger, csrf, filed["path"], "v3.pdf", V3).status_code == 404
    assert stranger.get(f"{filed['path']}/versions/1/download").status_code == 404


def test_old_versions_need_the_download_right_too(
    app: FastAPI, filed: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    add(filed["client"], filed["csrf"], filed["path"], "v2.pdf", V2)
    anchor = uuid.UUID(filed["folder"]["acl_anchor_id"])
    reader = new_user(db, tenant, "reader")
    allow(db, tenant, anchor, reader, "read")
    client, _ = sign_in(app, db, tenant, "reader")
    assert client.get(f"{filed['path']}/versions/1/download").status_code == 403
    assert client.get(f"{filed['path']}/versions").status_code == 200  # history is metadata


def test_a_missing_version_number_is_not_found(filed: dict[str, Any]) -> None:
    assert filed["client"].get(f"{filed['path']}/versions/9/download").status_code == 404


def test_an_archived_folder_accepts_no_new_versions(filed: dict[str, Any]) -> None:
    client, csrf, folder = filed["client"], filed["csrf"], filed["folder"]
    archived = client.patch(
        f"{API}/folders/{folder['id']}",
        json={"row_version": folder["row_version"], "status": "archived"},
        headers=csrf,
    )
    assert archived.status_code == 200, archived.text
    refused = add(client, csrf, filed["path"], "v2.pdf", V2)
    assert refused.status_code == 409
    assert "archived" in refused.json()["error"]["message"]


def test_another_organization_cannot_reach_the_history(
    app: FastAPI, filed: dict[str, Any], db: Database, other_tenant: uuid.UUID
) -> None:
    boss_of(db, other_tenant, "intruder")
    intruder, csrf = sign_in(app, db, other_tenant, "intruder")
    assert intruder.get(f"{filed['path']}/versions").status_code == 404
    assert add(intruder, csrf, filed["path"], "v2.pdf", V2).status_code == 404
