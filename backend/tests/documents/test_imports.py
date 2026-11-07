"""F03 ZIP import: safety of the archive, per-file results, rights, limits and retries."""

from __future__ import annotations

import io
import uuid
import zipfile
from dataclasses import replace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from docuflow.modules.documents.imports import (
    ImportLimits,
    plan_entries,
    safe_parts,
)
from docuflow.modules.documents.models import IngestionItem
from docuflow.modules.documents.scanner import Verdict
from docuflow.modules.identity.models import AclEntry, User
from docuflow.modules.identity.policy import bump_permission_epoch
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from docuflow.platform.jobs import HandlerRegistry, JobRunner
from docuflow.platform.models import ProcessingJob
from tests.documents.test_upload import PDF, make
from tests.identity.support import audit_events, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in


def build_zip(entries: dict[str, bytes], *extra: zipfile.ZipInfo) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        for info in extra:
            archive.writestr(info, b"target")
    return buffer.getvalue()


def pdf(label: str) -> bytes:
    return PDF + f"\n{label}\n".encode()


# The archive rules need no database.


@pytest.mark.parametrize(
    "name",
    ["../x.pdf", "/abs.pdf", "C:/x.pdf", "C:\\x.pdf", "a/../../b.pdf", "a\x00b.pdf", ""],
)
def test_paths_that_could_escape_are_unsafe(name: str) -> None:
    assert safe_parts(name) is None


def test_ordinary_paths_are_split_and_cleaned() -> None:
    assert safe_parts("Contracts\\2026//./a.pdf") == ("Contracts", "2026", "a.pdf")


def test_planning_refuses_the_dangerous_and_skips_the_junk() -> None:
    link = zipfile.ZipInfo("link.pdf")
    link.external_attr = 0o120777 << 16
    secret = zipfile.ZipInfo("secret.pdf")
    archive = build_zip(
        {
            "ok.pdf": PDF,
            "../escape.pdf": PDF,
            "__MACOSX/._ok.pdf": b"x",
            "dir/.DS_Store": b"x",
            "dir/Thumbs.db": b"x",
            "Ok.PDF": PDF,
        },
        link,
        secret,
    )
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        infos = zf.infolist()
    for info in infos:
        if info.filename == "secret.pdf":
            info.flag_bits |= 0x1  # the writer clears this flag, so set it on what was read back
    planned, refused = plan_entries(infos, ImportLimits())
    assert [item.path for item in planned] == ["ok.pdf"]
    reasons = {item.path: item.reason for item in refused}
    assert reasons["../escape.pdf"] == "The path is not safe."
    assert reasons["link.pdf"] == "Links are not imported."
    assert "Password-protected" in reasons["secret.pdf"]
    assert "more than once" in reasons["Ok.PDF"]


def test_extreme_compression_is_refused() -> None:
    archive = build_zip({"zeros.txt": b"0" * (3 * 1024 * 1024)})
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        planned, refused = plan_entries(zf.infolist(), ImportLimits(max_ratio=50))
    assert planned == []
    assert "compressed far more" in refused[0].reason


# The rest runs through the API, the job runner and the database.


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider, store: MemoryObjectStore) -> FastAPI:
    return make_app(db, audit, keys, store, max_upload_bytes=64 * 1024)


@pytest.fixture
def finance(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss = boss_of(db, tenant)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Incoming")
    return {"boss": boss, "client": client, "csrf": csrf, "cabinet": cabinet, "folder": folder}


def submit(
    client: TestClient,
    csrf: dict[str, str],
    kind: str,
    where: str,
    body: bytes,
    name: str = "bundle.zip",
):
    return client.post(
        f"{API}/{kind}/{where}/imports",
        params={"filename": name},
        content=body,
        headers={**csrf, "Content-Type": "application/octet-stream"},
    )


def run_import(app: FastAPI, db: Database, tenant: uuid.UUID, import_id: str) -> str:
    """Runs the queued job the way the worker does, through the real job runner."""
    registry = HandlerRegistry()
    registry.register("documents.import")(lambda job: app.state.imports.run(db, job))
    with db.tenant_session(tenant) as session:
        job_id = session.scalar(
            select(ProcessingJob.id).where(ProcessingJob.idempotency_key == f"import:{import_id}")
        )
    assert job_id is not None
    return JobRunner(db, registry, app.state.imports._audit, 0).run(tenant, job_id)


def children(client: TestClient, kind: str, where: str) -> dict:
    return client.get(f"{API}/{kind}/{where}/children").json()


@pytest.mark.db
def test_an_archive_becomes_folders_and_documents(
    app: FastAPI,
    finance: dict[str, Any],
    db: Database,
    tenant: uuid.UUID,
    store: MemoryObjectStore,
) -> None:
    client, csrf, folder = finance["client"], finance["csrf"], finance["folder"]
    body = build_zip(
        {
            "Contracts/2026/a.pdf": pdf("a"),
            "Contracts/b.pdf": pdf("b"),
            "root.pdf": pdf("root"),
            "notes.txt": b"plain notes",
        }
    )
    queued = submit(client, csrf, "folders", folder["id"], body)
    assert queued.status_code == 202, queued.text
    import_id = queued.json()["id"]
    assert queued.json()["status"] == "queued"
    assert queued.json()["file_count"] == 4
    assert len(store.objects) == 1  # only the staged archive so far

    assert run_import(app, db, tenant, import_id) == "succeeded"

    result = client.get(f"{API}/imports/{import_id}").json()
    assert (result["status"], result["filed"], result["failed"]) == ("completed", 4, 0)
    assert result["finished_at"] is not None
    assert len(store.objects) == 4  # the staged archive is gone, the four files remain

    top = children(client, "folders", folder["id"])
    assert sorted(item["name"] for item in top["folders"]) == ["Contracts"]
    assert sorted(item["title"] for item in top["documents"]["items"]) == ["notes.txt", "root.pdf"]
    contracts = next(item for item in top["folders"] if item["name"] == "Contracts")
    inside = children(client, "folders", contracts["id"])
    assert [item["title"] for item in inside["documents"]["items"]] == ["b.pdf"]
    year = inside["folders"][0]
    assert year["name"] == "2026"
    document = children(client, "folders", year["id"])["documents"]["items"][0]
    assert client.get(f"{API}/documents/{document['id']}/download").content == pdf("a")

    assert len(audit_events(db, tenant, "import.submitted")) == 1
    assert len(audit_events(db, tenant, "import.completed")) == 1


@pytest.mark.db
def test_bad_files_are_recorded_and_the_rest_are_filed(
    app: FastAPI, finance: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf, folder = finance["client"], finance["csrf"], finance["folder"]
    app.state.documents._scanner = type(
        "Scan",
        (),
        {
            "name": "test",
            "scan": lambda self, stream: Verdict(
                clean=b"VIRUS" not in stream.read(), signature="Test-Virus"
            ),
        },
    )()
    body = build_zip(
        {
            "good.pdf": pdf("good"),
            "fake.pdf": b"MZ this is a program",
            "virus.txt": b"VIRUS here",
            "../escape.pdf": pdf("escape"),
            "empty.txt": b"",
        }
    )
    import_id = submit(client, csrf, "folders", folder["id"], body).json()["id"]
    run_import(app, db, tenant, import_id)

    result = client.get(f"{API}/imports/{import_id}").json()
    assert (result["status"], result["filed"], result["failed"]) == ("completed", 1, 4)
    failed = {
        item["path"]: item["message"] for item in result["items"] if item["status"] == "failed"
    }
    assert "not accepted" in failed["fake.pdf"]
    assert "Test-Virus" in failed["virus.txt"]
    assert failed["../escape.pdf"] == "The path is not safe."
    assert "empty" in failed["empty.txt"]
    assert [item["status"] for item in result["items"]][:4] == ["failed"] * 4  # failures first
    top = children(client, "folders", folder["id"])
    assert [item["title"] for item in top["documents"]["items"]] == ["good.pdf"]


@pytest.mark.db
def test_repeated_content_in_an_archive_shares_one_object(
    app: FastAPI,
    finance: dict[str, Any],
    db: Database,
    tenant: uuid.UUID,
    store: MemoryObjectStore,
) -> None:
    client, csrf, folder = finance["client"], finance["csrf"], finance["folder"]
    body = build_zip({"one.pdf": pdf("same"), "two/copy.pdf": pdf("same")})
    import_id = submit(client, csrf, "folders", folder["id"], body).json()["id"]
    run_import(app, db, tenant, import_id)
    result = client.get(f"{API}/imports/{import_id}").json()
    assert (result["filed"], result["duplicates"]) == (2, 1)
    assert len(store.objects) == 1


@pytest.mark.db
def test_an_import_into_a_cabinet_works(
    app: FastAPI, finance: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf, cabinet = finance["client"], finance["csrf"], finance["cabinet"]
    import_id = submit(client, csrf, "cabinets", cabinet["id"], build_zip({"x/y.pdf": PDF})).json()[
        "id"
    ]
    run_import(app, db, tenant, import_id)
    names = [item["name"] for item in children(client, "cabinets", cabinet["id"])["folders"]]
    assert sorted(names) == ["Incoming", "x"]


@pytest.mark.db
def test_rights_decide_who_can_import_and_read_the_result(
    app: FastAPI, finance: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    folder = finance["folder"]
    anchor = uuid.UUID(folder["acl_anchor_id"])
    reader = new_user(db, tenant, "reader")
    allow(db, tenant, anchor, reader, "read")
    client, csrf = sign_in(app, db, tenant, "reader")
    body = build_zip({"a.pdf": PDF})
    assert submit(client, csrf, "folders", folder["id"], body).status_code == 403

    new_user(db, tenant, "stranger")
    stranger, stranger_csrf = sign_in(app, db, tenant, "stranger")
    assert submit(stranger, stranger_csrf, "folders", folder["id"], body).status_code == 404

    allow(db, tenant, anchor, reader, "create")
    queued = submit(client, csrf, "folders", folder["id"], body)
    assert queued.status_code == 202
    import_id = queued.json()["id"]
    assert client.get(f"{API}/imports/{import_id}").status_code == 200
    # The boss has every right, but an import belongs to the person who started it.
    assert finance["client"].get(f"{API}/imports/{import_id}").status_code == 404
    assert stranger.get(f"{API}/imports/{import_id}").status_code == 404


@pytest.mark.db
def test_the_importer_acts_with_the_submitters_rights_when_it_runs(
    app: FastAPI, finance: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    folder = finance["folder"]
    anchor = uuid.UUID(folder["acl_anchor_id"])
    helper = new_user(db, tenant, "helper")
    allow(db, tenant, anchor, helper, "read", "create")
    client, csrf = sign_in(app, db, tenant, "helper")
    import_id = submit(client, csrf, "folders", folder["id"], build_zip({"a.pdf": PDF})).json()[
        "id"
    ]
    # The right is taken away before the worker gets to it.
    with db.tenant_session(tenant) as session:
        session.execute(
            delete(AclEntry).where(
                AclEntry.anchor_id == anchor,
                AclEntry.principal_id == helper,
                AclEntry.permission == "create",
            )
        )
        bump_permission_epoch(session, tenant)
    run_import(app, db, tenant, import_id)
    result = client.get(f"{API}/imports/{import_id}").json()
    assert (result["filed"], result["failed"]) == (0, 1)
    assert "permission" in result["items"][0]["message"]


@pytest.mark.db
def test_an_import_by_a_disabled_person_does_not_run(
    app: FastAPI,
    finance: dict[str, Any],
    db: Database,
    tenant: uuid.UUID,
    store: MemoryObjectStore,
) -> None:
    client, csrf, folder = finance["client"], finance["csrf"], finance["folder"]
    import_id = submit(client, csrf, "folders", folder["id"], build_zip({"a.pdf": PDF})).json()[
        "id"
    ]
    with db.tenant_session(tenant) as session:
        session.execute(
            update(User).where(User.id == finance["boss"].user_id).values(status="disabled")
        )
    run_import(app, db, tenant, import_id)
    with db.tenant_session(tenant) as session:
        from docuflow.modules.documents.models import IngestionJob

        job = session.scalar(select(IngestionJob).where(IngestionJob.id == uuid.UUID(import_id)))
    assert job is not None
    assert job.status == "failed"
    assert "can no longer sign in" in (job.error or "")
    assert store.objects == {}  # nothing filed, and the staged archive was removed


@pytest.mark.db
def test_submission_refuses_what_is_not_a_usable_archive(
    app: FastAPI, finance: dict[str, Any], store: MemoryObjectStore
) -> None:
    client, csrf, folder = finance["client"], finance["csrf"], finance["folder"]
    where = folder["id"]
    assert submit(client, csrf, "folders", where, PDF).status_code == 415
    assert submit(client, csrf, "folders", where, b"PK\x03\x04 not really a zip").status_code == 400
    assert submit(client, csrf, "folders", where, b"").status_code == 400

    body = build_zip({"a.pdf": PDF, "b.pdf": pdf("b"), "c.pdf": pdf("c")})
    app.state.imports.limits = replace(app.state.imports.limits, max_entries=2)
    refused = submit(client, csrf, "folders", where, body)
    assert refused.status_code == 400
    assert "more than 2 entries" in refused.json()["error"]["message"]

    app.state.imports.limits = replace(
        app.state.imports.limits, max_entries=100, max_archive_bytes=50
    )
    assert submit(client, csrf, "folders", where, body).status_code == 413
    assert store.objects == {}  # nothing was staged for any of them


@pytest.mark.db
def test_an_archive_that_expands_too_far_stops_filing(
    app: FastAPI, finance: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf, folder = finance["client"], finance["csrf"], finance["folder"]
    body = build_zip({f"f{index}.pdf": pdf(f"file {index}" * 200) for index in range(4)})
    app.state.imports.limits = replace(app.state.imports.limits, max_expanded_bytes=3000)
    import_id = submit(client, csrf, "folders", folder["id"], body).json()["id"]
    run_import(app, db, tenant, import_id)
    result = client.get(f"{API}/imports/{import_id}").json()
    assert result["filed"] >= 1
    assert result["failed"] >= 1
    assert any("expands to more" in item["message"] for item in result["items"])


@pytest.mark.db
def test_a_retry_skips_files_that_are_already_done(
    app: FastAPI, finance: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf, folder = finance["client"], finance["csrf"], finance["folder"]
    body = build_zip({"a.pdf": pdf("a"), "b.pdf": pdf("b")})
    import_id = submit(client, csrf, "folders", folder["id"], body).json()["id"]
    # A first attempt filed a.pdf and then died.
    with db.tenant_session(tenant) as session:
        session.add(
            IngestionItem(
                tenant_id=tenant,
                job_id=uuid.UUID(import_id),
                path="a.pdf",
                status="filed",
                message="",
            )
        )
    run_import(app, db, tenant, import_id)
    result = client.get(f"{API}/imports/{import_id}").json()
    assert (result["status"], result["filed"], result["failed"]) == ("completed", 2, 0)
    titles = [
        item["title"] for item in children(client, "folders", folder["id"])["documents"]["items"]
    ]
    assert titles == ["b.pdf"]  # a.pdf was not filed a second time
