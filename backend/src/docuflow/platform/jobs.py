"""Durable background jobs (ADR 0007).

PostgreSQL holds the truth and Celery only executes. Delivery is at-least-once, so the runner
claims each job under a row lock and fences completion by attempt number. A redelivered message,
or a worker that wakes up after being presumed dead, therefore cannot corrupt state.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from docuflow.platform import context
from docuflow.platform.audit import AuditLog
from docuflow.platform.db import Database, utcnow
from docuflow.platform.ids import uuid7
from docuflow.platform.models import Outbox, ProcessingJob, Timer

Queue = Literal["ingest", "ocr", "index", "embed", "compress", "notify", "integrations", "system"]
DISPATCH_TOPIC = "job.dispatch"
MAX_ERROR_LENGTH = 2000
REDISPATCH_AFTER = timedelta(seconds=60)
BATCH_SIZE = 200


@dataclass(frozen=True)
class JobContext:
    tenant_id: uuid.UUID
    job_id: uuid.UUID
    payload: dict[str, Any]
    report: Callable[[int], None]


JobHandler = Callable[[JobContext], None]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str) -> Callable[[JobHandler], JobHandler]:
        def decorator(handler: JobHandler) -> JobHandler:
            if job_type in self._handlers:
                raise ValueError(f"handler already registered for {job_type}")
            self._handlers[job_type] = handler
            return handler

        return decorator

    def get(self, job_type: str) -> JobHandler | None:
        return self._handlers.get(job_type)


def enqueue(
    session: Session,
    tenant_id: uuid.UUID,
    job_type: str,
    idempotency_key: str,
    *,
    queue: Queue = "system",
    payload: dict[str, Any] | None = None,
    max_attempts: int = 5,
    run_at: datetime | None = None,
) -> tuple[uuid.UUID, bool]:
    """Create a job in the caller's transaction. Returns (job id, whether it is new)."""
    now = utcnow()
    job_id = uuid7()
    created = session.execute(
        insert(ProcessingJob)
        .values(
            id=job_id,
            tenant_id=tenant_id,
            type=job_type,
            queue=queue,
            status="queued",
            idempotency_key=idempotency_key,
            payload=payload or {},
            attempts=0,
            max_attempts=max_attempts,
            progress=0,
            correlation_id=context.correlation_id.get(),
            next_run_at=run_at or now,
            dispatched_at=now,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
        .returning(ProcessingJob.id)
    ).scalar_one_or_none()
    if created is None:
        existing = session.execute(
            select(ProcessingJob.id).where(
                ProcessingJob.tenant_id == tenant_id,
                ProcessingJob.idempotency_key == idempotency_key,
            )
        ).scalar_one()
        return existing, False
    _add_dispatch(session, tenant_id, job_id, queue)
    return job_id, True


def _add_dispatch(session: Session, tenant_id: uuid.UUID, job_id: uuid.UUID, queue: str) -> None:
    session.add(
        Outbox(
            tenant_id=tenant_id,
            topic=DISPATCH_TOPIC,
            payload={"job_id": str(job_id), "tenant_id": str(tenant_id), "queue": queue},
        )
    )


class JobRunner:
    """Runs one delivered job. Called by the Celery task, and directly by tests."""

    def __init__(
        self,
        db: Database,
        registry: HandlerRegistry,
        audit: AuditLog,
        retry_base_seconds: int = 30,
    ) -> None:
        self._db = db
        self._registry = registry
        self._audit = audit
        self._retry_base = retry_base_seconds

    def run(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> str:
        claimed = self._claim(tenant_id, job_id)
        if claimed is None:
            return "skipped"
        job_type, payload, attempt, correlation = claimed
        token = context.correlation_id.set(correlation)
        try:
            handler = self._registry.get(job_type)
            if handler is None:
                raise LookupError(f"no handler registered for job type {job_type}")
            handler(
                JobContext(
                    tenant_id,
                    job_id,
                    payload,
                    lambda percent: self._report(tenant_id, job_id, attempt, percent),
                )
            )
        except Exception as exc:  # any handler failure must reach the retry path
            return self._fail(tenant_id, job_id, attempt, exc)
        else:
            return self._succeed(tenant_id, job_id, attempt)
        finally:
            context.correlation_id.reset(token)

    def _claim(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID
    ) -> tuple[str, dict[str, Any], int, str | None] | None:
        now = utcnow()
        with self._db.tenant_session(tenant_id) as session:
            job = session.execute(
                select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()
            ).scalar_one_or_none()
            if job is None or job.status != "queued" or job.next_run_at > now:
                return None
            job.status = "running"
            job.attempts += 1
            job.heartbeat_at = now
            job.last_error = None
            return job.type, job.payload, job.attempts, job.correlation_id

    def _report(self, tenant_id: uuid.UUID, job_id: uuid.UUID, attempt: int, percent: int) -> None:
        """Progress doubles as the heartbeat that keeps the sweeper away."""
        with self._db.tenant_session(tenant_id) as session:
            session.execute(
                update(ProcessingJob)
                .where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.attempts == attempt,
                    ProcessingJob.status == "running",
                )
                .values(progress=max(0, min(100, percent)), heartbeat_at=utcnow())
            )

    def _succeed(self, tenant_id: uuid.UUID, job_id: uuid.UUID, attempt: int) -> str:
        with self._db.tenant_session(tenant_id) as session:
            changed = session.execute(
                update(ProcessingJob)
                .where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.attempts == attempt,
                    ProcessingJob.status == "running",
                )
                .values(status="succeeded", progress=100, finished_at=utcnow())
            )
        return "succeeded" if changed.rowcount else "fenced"  # type: ignore[attr-defined]

    def _fail(self, tenant_id: uuid.UUID, job_id: uuid.UUID, attempt: int, exc: Exception) -> str:
        error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
        now = utcnow()
        with self._db.tenant_session(tenant_id) as session:
            job = session.execute(
                select(ProcessingJob)
                .where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.attempts == attempt,
                    ProcessingJob.status == "running",
                )
                .with_for_update()
            ).scalar_one_or_none()
            if job is None:
                return "fenced"
            job.last_error = error
            if job.attempts >= job.max_attempts:
                job.status = "dead"
                job.finished_at = now
                self._audit.record(
                    session,
                    tenant_id,
                    "job.dead",
                    object_type="processing_job",
                    object_id=str(job_id),
                    result="failure",
                    details={"type": job.type, "attempts": job.attempts},
                )
                return "dead"
            job.status = "queued"
            job.next_run_at = now + timedelta(seconds=self._retry_base * 2 ** (attempt - 1))
            job.dispatched_at = None
            return "retry"


class Dispatcher:
    """Moves committed outbox rows to the broker. Safe to run as several processes."""

    def __init__(self, db: Database, publish: Callable[[str, dict[str, Any]], None]) -> None:
        self._db = db
        self._publish = publish

    def publish_batch(self) -> int:
        with self._db.platform_session() as session:
            rows = list(
                session.scalars(
                    select(Outbox)
                    .where(Outbox.published_at.is_(None))
                    .order_by(Outbox.created_at)
                    .limit(BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
            )
            now = utcnow()
            for row in rows:
                self._publish(row.topic, row.payload)
                row.published_at = now
            return len(rows)


class Maintenance:
    """Periodic recovery. Losing Redis loses no work, because this rebuilds what was queued."""

    def __init__(self, db: Database, audit: AuditLog, heartbeat_timeout_seconds: int) -> None:
        self._db = db
        self._audit = audit
        self._timeout = timedelta(seconds=heartbeat_timeout_seconds)

    def tick(self) -> dict[str, int]:
        return {
            "timers_fired": self.fire_timers(),
            "recovered": self.recover_stalled(),
            "redispatched": self.redispatch_queued(),
        }

    def fire_timers(self) -> int:
        """Each due timer becomes a job, so handlers share one retry path."""
        with self._db.platform_session() as session:
            timers = list(
                session.scalars(
                    select(Timer)
                    .where(Timer.status == "pending", Timer.fire_at <= utcnow())
                    .limit(BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
            )
            for timer in timers:
                enqueue(
                    session,
                    timer.tenant_id,
                    timer.kind,
                    f"timer:{timer.id}",
                    payload=timer.payload,
                )
                timer.status = "fired"
            return len(timers)

    def recover_stalled(self) -> int:
        cutoff = utcnow() - self._timeout
        with self._db.platform_session() as session:
            jobs = list(
                session.scalars(
                    select(ProcessingJob)
                    .where(ProcessingJob.status == "running", ProcessingJob.heartbeat_at < cutoff)
                    .limit(BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
            )
            for job in jobs:
                job.last_error = "worker heartbeat expired"
                if job.attempts >= job.max_attempts:
                    job.status = "dead"
                    job.finished_at = utcnow()
                    self._audit.record(
                        session,
                        job.tenant_id,
                        "job.dead",
                        object_type="processing_job",
                        object_id=str(job.id),
                        result="failure",
                        details={"type": job.type, "attempts": job.attempts},
                    )
                else:
                    job.status = "queued"
                    job.next_run_at = utcnow()
                    job.dispatched_at = None
            return len(jobs)

    def redispatch_queued(self) -> int:
        """Covers retries that have come due, and messages lost from the broker."""
        now = utcnow()
        with self._db.platform_session() as session:
            jobs = list(
                session.scalars(
                    select(ProcessingJob)
                    .where(
                        ProcessingJob.status == "queued",
                        ProcessingJob.next_run_at <= now,
                        or_(
                            ProcessingJob.dispatched_at.is_(None),
                            ProcessingJob.dispatched_at < now - REDISPATCH_AFTER,
                        ),
                    )
                    .limit(BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
            )
            for job in jobs:
                _add_dispatch(session, job.tenant_id, job.id, job.queue)
                job.dispatched_at = now
            return len(jobs)
