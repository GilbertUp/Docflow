"""Celery wiring. Only this file knows about Celery (ADR 0007)."""

from __future__ import annotations

import uuid
from typing import Any

from celery import Celery

from docuflow.platform.bootstrap import get_services
from docuflow.platform.jobs import DISPATCH_TOPIC, HandlerRegistry, JobContext, JobRunner

registry = HandlerRegistry()


@registry.register("audit.checkpoint")
def audit_checkpoint(job: JobContext) -> None:
    services = get_services()
    with services.db.tenant_session(job.tenant_id) as session:
        services.audit.write_checkpoint(session, job.tenant_id, services.store)


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


def publish(topic: str, payload: dict[str, Any]) -> None:
    if topic != DISPATCH_TOPIC:
        raise ValueError(f"no route for outbox topic {topic}")
    celery_app.send_task(
        "docuflow.run_job",
        args=[payload["tenant_id"], payload["job_id"]],
        queue=payload["queue"],
    )
