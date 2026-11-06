"""Celery wiring, without a broker. A real end-to-end run happens in the compose gate."""

from __future__ import annotations

import base64
import importlib
import os
import uuid
from collections.abc import Iterator
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from docuflow.platform.bootstrap import get_services
from docuflow.platform.config import get_settings
from docuflow.platform.jobs import DISPATCH_TOPIC


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    values = {
        "DOCUFLOW_DATABASE_URL": "postgresql+psycopg://a:b@localhost:1/c",
        "DOCUFLOW_PLATFORM_DATABASE_URL": "postgresql+psycopg://a:b@localhost:1/c",
        "DOCUFLOW_REDIS_URL": "redis://localhost:1/0",
        "DOCUFLOW_S3_ENDPOINT": "http://localhost:1",
        "DOCUFLOW_S3_ACCESS_KEY": "a",
        "DOCUFLOW_S3_SECRET_KEY": "b",
        "DOCUFLOW_MASTER_KEY": base64.b64encode(os.urandom(32)).decode(),
        "DOCUFLOW_AUDIT_HMAC_KEY": base64.b64encode(os.urandom(32)).decode(),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    get_services.cache_clear()
    yield importlib.import_module("docuflow.worker")
    get_settings.cache_clear()
    get_services.cache_clear()


def test_task_is_registered_with_safe_delivery(worker: ModuleType) -> None:
    assert "docuflow.run_job" in worker.celery_app.tasks
    assert worker.celery_app.conf.task_acks_late is True
    assert worker.celery_app.conf.task_reject_on_worker_lost is True
    assert worker.celery_app.conf.worker_prefetch_multiplier == 1
    assert worker.celery_app.conf.accept_content == ["json"]


def test_checkpoint_handler_is_registered(worker: ModuleType) -> None:
    assert worker.registry.get("audit.checkpoint") is not None


def test_publish_routes_to_the_jobs_queue(
    worker: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    send_task = MagicMock()
    monkeypatch.setattr(worker.celery_app, "send_task", send_task)
    tenant, job = str(uuid.uuid4()), str(uuid.uuid4())
    worker.publish(DISPATCH_TOPIC, {"tenant_id": tenant, "job_id": job, "queue": "ocr"})
    send_task.assert_called_once_with("docuflow.run_job", args=[tenant, job], queue="ocr")


def test_unknown_topic_is_refused(worker: ModuleType) -> None:
    with pytest.raises(ValueError, match="no route"):
        worker.publish("unknown.topic", {})
