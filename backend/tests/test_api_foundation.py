"""Health, correlation IDs, security headers and error handling."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from docuflow.main import create_app, readiness
from docuflow.platform.db import Database


def fake_services(db: Any, redis_ok: bool = True, store_ok: bool = True) -> Any:
    redis = MagicMock()
    store = MagicMock()
    if not redis_ok:
        redis.ping.side_effect = ConnectionError("down")
    if not store_ok:
        store.check.side_effect = ConnectionError("down")
    return SimpleNamespace(
        settings=SimpleNamespace(log_level="WARNING", environment="test"),
        db=db,
        audit=MagicMock(),
        keys=MagicMock(),
        redis=redis,
        store=store,
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(fake_services(MagicMock())), raise_server_exceptions=False)


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_security_headers_are_present(client: TestClient) -> None:
    headers = client.get("/health/live").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in headers["content-security-policy"]
    assert "strict-transport-security" in headers


def test_correlation_id_is_generated(client: TestClient) -> None:
    assert len(client.get("/health/live").headers["x-correlation-id"]) >= 8


def test_valid_correlation_id_is_kept(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Correlation-ID": "abc-12345"})
    assert response.headers["x-correlation-id"] == "abc-12345"


def test_malicious_correlation_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Correlation-ID": "<script>alert(1)</script>"})
    assert "script" not in response.headers["x-correlation-id"]


def test_unhandled_error_reveals_nothing() -> None:
    app: FastAPI = create_app(fake_services(MagicMock()))

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert "secret internal detail" not in response.text
    assert response.json()["correlation_id"] == response.headers["x-correlation-id"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_openapi_is_versioned(client: TestClient) -> None:
    assert client.get("/api/v1/openapi.json").status_code == 200


def test_metrics_are_exposed(client: TestClient) -> None:
    client.get("/health/live")
    assert "docuflow_http_requests_total" in client.get("/metrics").text


def test_ready_when_dependencies_are_up(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200


def test_not_ready_when_no_worker_is_alive() -> None:
    services = fake_services(MagicMock())
    services.redis.exists.return_value = 0
    response = TestClient(create_app(services)).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["workers"] == "unavailable"


def test_not_ready_when_a_dependency_is_down() -> None:
    client = TestClient(create_app(fake_services(MagicMock(), redis_ok=False)))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["queue"] == "unavailable"


@pytest.mark.db
def test_readiness_uses_a_real_database(db: Database) -> None:
    assert readiness(fake_services(db))["database"] == "ok"
