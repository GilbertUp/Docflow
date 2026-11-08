"""Celery wiring. Only this file knows about Celery (ADR 0007)."""

from __future__ import annotations

import threading
import uuid
from typing import Any

import structlog
from celery import Celery
from celery.signals import worker_ready
from redis.exceptions import RedisError

from docuflow.modules.documents.extraction import JOB_TYPE as EXTRACT_JOB
from docuflow.modules.documents.wiring import build_documents
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.indexing.suggest import JOB_TYPE as FIELDS_JOB
from docuflow.modules.indexing.suggest import SuggestionService
from docuflow.modules.repository.service import RepositoryService
from docuflow.modules.search.service import JOB_TYPE as SEARCH_JOB
from docuflow.modules.search.service import SearchService
from docuflow.platform.bootstrap import get_services
from docuflow.platform.jobs import (
    DISPATCH_TOPIC,
    WORKER_HEARTBEAT_INTERVAL_SECONDS,
    WORKER_HEARTBEAT_KEY,
    WORKER_HEARTBEAT_TTL_SECONDS,
    HandlerRegistry,
    JobContext,
    JobRunner,
)

registry = HandlerRegistry()
log = structlog.get_logger("docuflow.worker")


@registry.register("audit.checkpoint")
def audit_checkpoint(job: JobContext) -> None:
    services = get_services()
    with services.db.tenant_session(job.tenant_id) as session:
        services.audit.write_checkpoint(session, job.tenant_id, services.store)


@registry.register("documents.import")
def documents_import(job: JobContext) -> None:
    services = get_services()
    policy = PolicyService()
    repo = RepositoryService(services.audit, policy)
    build_documents(services, repo, policy).imports.run(services.db, job)


@registry.register(EXTRACT_JOB)
def documents_extract_text(job: JobContext) -> None:
    services = get_services()
    policy = PolicyService()
    repo = RepositoryService(services.audit, policy)
    build_documents(services, repo, policy).text.run(services.db, job)


@registry.register(FIELDS_JOB)
def indexing_extract_fields(job: JobContext) -> None:
    services = get_services()
    policy = PolicyService()
    repo = RepositoryService(services.audit, policy)
    SuggestionService(services.audit, policy, repo).run(services.db, job)


@registry.register(SEARCH_JOB)
def search_index_document(job: JobContext) -> None:
    services = get_services()
    SearchService(services.audit, PolicyService()).run(services.db, job)


def make_celery(broker_url: str) -> Celery:
    app = Celery("docuflow", broker=broker_url)
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_ignore_result=True,
        task_serializer="json",
        accept_content=["json"],
        broker_connection_retry_on_startup=True,
        task_default_queue="system",
    )
    return app


celery_app = make_celery(get_services().settings.redis_url)


@celery_app.task(name="docuflow.run_job")  # type: ignore[untyped-decorator]
def run_job(tenant_id: str, job_id: str) -> str:
    services = get_services()
    runner = JobRunner(
        services.db,
        registry,
        services.audit,
        services.settings.job_retry_base_seconds,
    )
    return runner.run(uuid.UUID(tenant_id), uuid.UUID(job_id))


def beat_once() -> None:
    """Readiness reads this key, so a dead worker cannot look healthy."""
    get_services().redis.set(WORKER_HEARTBEAT_KEY, "1", ex=WORKER_HEARTBEAT_TTL_SECONDS)


def _heartbeat_loop(stop: threading.Event) -> None:
    while not stop.wait(WORKER_HEARTBEAT_INTERVAL_SECONDS):
        try:
            beat_once()
        except RedisError:
            log.warning("worker_heartbeat_failed")


@worker_ready.connect  # type: ignore[untyped-decorator]
def start_heartbeat(**_: Any) -> None:
    beat_once()
    threading.Thread(
        target=_heartbeat_loop, args=(threading.Event(),), name="heartbeat", daemon=True
    ).start()


def publish(topic: str, payload: dict[str, Any]) -> None:
    if topic != DISPATCH_TOPIC:
        raise ValueError(f"no route for outbox topic {topic}")
    celery_app.send_task(
        "docuflow.run_job",
        args=[payload["tenant_id"], payload["job_id"]],
        queue=payload["queue"],
    )
