"""A real app wired to the test database, plus cookie-and-CSRF sign-in for HTTP tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from docuflow.main import create_app
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
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


def make_app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    services = SimpleNamespace(
        settings=SimpleNamespace(log_level="WARNING", environment="test"),
        db=db,
        audit=audit,
        keys=keys,
        redis=FakeRedis(),
        store=MagicMock(),
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
