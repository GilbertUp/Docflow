"""The clamd client against a stand-in server, and what an upload does with each verdict."""

from __future__ import annotations

import io
import socket
import struct
import threading
import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from docuflow.modules.documents.models import BinaryObject, DocumentVersion
from docuflow.modules.documents.scanner import (
    ClamdScanner,
    NoScanner,
    ScannerUnavailable,
    Verdict,
    build_scanner,
)
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from tests.documents.test_upload import LIMIT, PDF, make, put
from tests.identity.support import audit_events
from tests.repository.support import boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class FakeClamd:
    """Parses INSTREAM like clamd does, and answers by a simple rule."""

    def __init__(self, reply: bytes | None = None) -> None:
        self.reply = reply
        self.received = b""
        self.command = b""
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen()
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @staticmethod
    def _read(connection: socket.socket, count: int) -> bytes:
        data = b""
        while len(data) < count:
            part = connection.recv(count - len(data))
            if not part:
                break
            data += part
        return data

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            with connection:
                self.command = self._read(connection, len(b"zINSTREAM\0"))
                body = b""
                while True:
                    (length,) = struct.unpack("!I", self._read(connection, 4))
                    if length == 0:
                        break
                    body += self._read(connection, length)
                self.received = body
                if self.reply is not None:
                    connection.sendall(self.reply)
                elif b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in body:
                    connection.sendall(b"stream: Eicar-Test-Signature FOUND\0")
                else:
                    connection.sendall(b"stream: OK\0")

    def close(self) -> None:
        self._server.close()


@pytest.fixture
def clamd() -> Iterator[FakeClamd]:
    server = FakeClamd()
    yield server
    server.close()


def test_a_clean_stream_is_cleared(clamd: FakeClamd) -> None:
    verdict = ClamdScanner("127.0.0.1", clamd.port).scan(io.BytesIO(b"hello"))
    assert verdict == Verdict(clean=True)
    assert clamd.command == b"zINSTREAM\0"


def test_a_virus_is_named(clamd: FakeClamd) -> None:
    verdict = ClamdScanner("127.0.0.1", clamd.port).scan(io.BytesIO(b"prefix" + EICAR))
    assert verdict == Verdict(clean=False, signature="Eicar-Test-Signature")


def test_a_large_stream_arrives_whole_in_chunks(clamd: FakeClamd) -> None:
    data = bytes(range(256)) * 2048  # 512 KiB, several protocol chunks
    ClamdScanner("127.0.0.1", clamd.port).scan(io.BytesIO(data))
    assert clamd.received == data


@pytest.mark.parametrize(
    "reply",
    [b"INSTREAM size limit exceeded. ERROR\0", b"stream: something odd\0", b""],
)
def test_an_answer_that_is_not_clean_or_found_is_unavailable(reply: bytes) -> None:
    server = FakeClamd(reply)
    try:
        with pytest.raises(ScannerUnavailable):
            ClamdScanner("127.0.0.1", server.port).scan(io.BytesIO(b"x"))
    finally:
        server.close()


def test_a_scanner_that_cannot_be_reached_is_unavailable() -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # nothing listens here now
    with pytest.raises(ScannerUnavailable):
        ClamdScanner("127.0.0.1", port, timeout_seconds=2).scan(io.BytesIO(b"x"))


def test_settings_choose_the_scanner() -> None:
    class Chosen:
        scanner = "clamav"
        clamav_host = "h"
        clamav_port = 1
        clamav_timeout_seconds = 5.0

    assert isinstance(build_scanner(Chosen()), ClamdScanner)
    assert isinstance(build_scanner(object()), NoScanner)


class Fixed:
    def __init__(self, verdict: Verdict | None, name: str = "clamav") -> None:
        self.verdict = verdict
        self.name = name

    def scan(self, stream: object) -> Verdict:
        if self.verdict is None:
            raise ScannerUnavailable("Files cannot be checked for viruses right now.")
        return self.verdict


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider, store: MemoryObjectStore) -> FastAPI:
    return make_app(db, audit, keys, store, max_upload_bytes=LIMIT)


@pytest.fixture
def folder(app: FastAPI, db: Database, tenant: uuid.UUID) -> tuple[TestClient, dict, dict]:
    boss_of(db, tenant)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    made = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Invoices")
    return client, csrf, made


def count_documents(client: TestClient, folder_id: str) -> int:
    return int(client.get(f"{API}/folders/{folder_id}/children").json()["documents"]["total"])


@pytest.mark.db
def test_an_infected_upload_is_refused_recorded_and_never_stored(
    app: FastAPI,
    folder: tuple[TestClient, dict, dict],
    db: Database,
    tenant: uuid.UUID,
    store: MemoryObjectStore,
) -> None:
    client, csrf, made = folder
    app.state.documents._scanner = Fixed(Verdict(clean=False, signature="Eicar-Test-Signature"))
    response = put(client, csrf, made["id"], "invoice.pdf", PDF)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "malware_detected"
    assert "Eicar-Test-Signature" in response.json()["error"]["message"]
    assert store.objects == {}
    assert count_documents(client, made["id"]) == 0
    with db.tenant_session(tenant) as session:
        assert session.scalars(select(BinaryObject)).all() == []
    (event,) = audit_events(db, tenant, "document.upload_blocked")
    assert event.object_type == "upload"
    assert "Eicar-Test-Signature" in str(event.details)


@pytest.mark.db
def test_an_unavailable_scanner_refuses_the_upload_instead_of_releasing_it(
    app: FastAPI, folder: tuple[TestClient, dict, dict], store: MemoryObjectStore
) -> None:
    client, csrf, made = folder
    app.state.documents._scanner = Fixed(None)
    response = put(client, csrf, made["id"], "invoice.pdf", PDF)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "scanner_unavailable"
    assert store.objects == {}
    assert count_documents(client, made["id"]) == 0


@pytest.mark.db
def test_the_version_records_which_scanner_cleared_it(
    app: FastAPI, folder: tuple[TestClient, dict, dict], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf, made = folder
    unscanned = put(client, csrf, made["id"], "a.pdf", PDF).json()
    assert unscanned["version"]["scan_engine"] == "none"
    app.state.documents._scanner = Fixed(Verdict(clean=True))
    scanned = put(client, csrf, made["id"], "b.pdf", PDF + b" different").json()
    assert scanned["version"]["scan_engine"] == "clamav"
    with db.tenant_session(tenant) as session:
        engines = sorted(session.scalars(select(DocumentVersion.scan_engine)))
    assert engines == ["clamav", "none"]
