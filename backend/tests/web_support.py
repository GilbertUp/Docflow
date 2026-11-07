"""A real app wired to the test database, plus cookie-and-CSRF sign-in for HTTP tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import BinaryIO

from fastapi import FastAPI
from fastapi.testclient import TestClient

from docuflow.main import create_app
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from docuflow.platform.storage import EncryptedObjects
from tests.identity.support import STRONG, email, tenant_slug

API = "/api/v1"


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def incr(self, name: str) -> int:
        self.counts[name] = self.counts.get(name, 0) + 1
        return self.counts[name]

    def expire(self, name: str, seconds: int) -> bool:
        return True

    def ping(self) -> bool:
        return True

    def exists(self, key: str) -> int:
        return 1


class MemoryObjectStore:
    """An ObjectStore that keeps bytes in memory, so upload tests need no S3 server."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, body: BinaryIO, length: int) -> None:
        self.objects[key] = body.read()

    def exists(self, key: str) -> bool:
        return key in self.objects

    def size(self, key: str) -> int:
        return len(self.objects[key])

    def get(self, key: str) -> Iterator[bytes]:
        yield self.objects[key]

    def get_range(self, key: str, start: int, end: int) -> bytes:
        return self.objects[key][start : end + 1]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def check(self) -> None:
        return None


def make_app(
    db: Database,
    audit: AuditLog,
    keys: LocalKeyProvider,
    store: MemoryObjectStore | None = None,
    max_upload_bytes: int = 5 * 1024 * 1024,
) -> FastAPI:
    store = store or MemoryObjectStore()
    services = SimpleNamespace(
        settings=SimpleNamespace(
            log_level="WARNING", environment="test", max_upload_bytes=max_upload_bytes
        ),
        db=db,
        audit=audit,
        keys=keys,
        redis=FakeRedis(),
        store=store,
        objects=EncryptedObjects(store, keys),
    )
    return create_app(services)  # type: ignore[arg-type]


def sign_in(
    app: FastAPI, db: Database, tenant: uuid.UUID, name: str, password: str = STRONG
) -> tuple[TestClient, dict[str, str]]:
    client = TestClient(app)
    response = client.post(
        f"{API}/auth/login",
        json={"organization": tenant_slug(db, tenant), "email": email(name), "password": password},
    )
    assert response.status_code == 200, response.text
    return client, {"X-CSRF-Token": response.json()["csrf_token"] or ""}
