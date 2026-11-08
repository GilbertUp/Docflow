"""ADR 0007: durability, idempotency, retry, recovery and fencing."""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from docuflow.platform.audit import AuditLog
from docuflow.platform.db import Database, utcnow
from docuflow.platform.jobs import (
    Dispatcher,
    HandlerRegistry,
    JobContext,
    JobRunner,
    Maintenance,
    enqueue,
)
from docuflow.platform.models import Outbox, ProcessingJob, Timer

pytestmark = pytest.mark.db


def job_state(db: Database, tenant: uuid.UUID, job_id: uuid.UUID) -> ProcessingJob:
    with db.tenant_session(tenant) as session:
        return session.execute(select(ProcessingJob).where(ProcessingJob.id == job_id)).scalar_one()


def make_job(db: Database, tenant: uuid.UUID, job_type: str = "t", **kwargs: Any) -> uuid.UUID:
    with db.tenant_session(tenant) as session:
        job_id, _ = enqueue(session, tenant, job_type, uuid.uuid4().hex, **kwargs)
    return job_id


def set_job(db: Database, tenant: uuid.UUID, job_id: uuid.UUID, **values: Any) -> None:
    with db.tenant_session(tenant) as session:
        session.execute(update(ProcessingJob).where(ProcessingJob.id == job_id).values(**values))


def quiet_other_tenants(db: Database, tenant: uuid.UUID) -> None:
    """Other tests leave queued jobs behind. A tick takes at most one batch of them across every
    organization, so mark them as sent to keep this test about its own job."""
    with db.platform_session() as session:
        session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.tenant_id != tenant, ProcessingJob.status == "queued")
            .values(dispatched_at=utcnow())
        )


def make_runner(db: Database, audit: AuditLog, handler: Any, job_type: str = "t") -> JobRunner:
    registry = HandlerRegistry()
    registry.register(job_type)(handler)
    return JobRunner(db, registry, audit, retry_base_seconds=0)


def test_enqueue_is_idempotent(db: Database, tenant: uuid.UUID) -> None:
    with db.tenant_session(tenant) as session:
        first, created_first = enqueue(session, tenant, "t", "same-key")
        second, created_second = enqueue(session, tenant, "t", "same-key")
    assert first == second
    assert (created_first, created_second) == (True, False)
    with db.tenant_session(tenant) as session:
        assert len(session.scalars(select(Outbox)).all()) == 1


def test_rolled_back_transaction_creates_no_job(db: Database, tenant: uuid.UUID) -> None:
    with pytest.raises(RuntimeError), db.tenant_session(tenant) as session:  # noqa: PT012
        enqueue(session, tenant, "t", "rolled-back")
        raise RuntimeError("business failure")
    with db.tenant_session(tenant) as session:
        assert session.scalars(select(ProcessingJob)).all() == []
        assert session.scalars(select(Outbox)).all() == []


def test_dispatcher_publishes_once(db: Database, tenant: uuid.UUID) -> None:
    make_job(db, tenant)
    published: list[dict[str, Any]] = []
    dispatcher = Dispatcher(db, lambda _topic, payload: published.append(payload))
    assert dispatcher.publish_batch() >= 1
    dispatcher.publish_batch()
    assert len([p for p in published if p["tenant_id"] == str(tenant)]) == 1


def test_failed_publish_keeps_events_for_retry(db: Database, tenant: uuid.UUID) -> None:
    make_job(db, tenant)

    def broken(_topic: str, _payload: dict[str, Any]) -> None:
        raise ConnectionError("broker down")

    with pytest.raises(ConnectionError):
        Dispatcher(db, broken).publish_batch()
    published: list[dict[str, Any]] = []
    Dispatcher(db, lambda _t, p: published.append(p)).publish_batch()
    assert any(p["tenant_id"] == str(tenant) for p in published)


def test_success_updates_state(db: Database, audit: AuditLog, tenant: uuid.UUID) -> None:
    seen: list[dict[str, Any]] = []

    def handler(job: JobContext) -> None:
        job.report(50)
        seen.append(job.payload)

    job_id = make_job(db, tenant, payload={"x": 1})
    assert make_runner(db, audit, handler).run(tenant, job_id) == "succeeded"
    state = job_state(db, tenant, job_id)
    assert (state.status, state.progress, state.attempts) == ("succeeded", 100, 1)
    assert seen == [{"x": 1}]


def test_duplicate_delivery_runs_once(db: Database, audit: AuditLog, tenant: uuid.UUID) -> None:
    calls: list[int] = []
    runner = make_runner(db, audit, lambda _job: calls.append(1))
    job_id = make_job(db, tenant)
    assert runner.run(tenant, job_id) == "succeeded"
    assert runner.run(tenant, job_id) == "skipped"
    assert len(calls) == 1


def test_concurrent_delivery_runs_once(db: Database, audit: AuditLog, tenant: uuid.UUID) -> None:
    calls: list[int] = []
    gate = threading.Barrier(4)
    runner = make_runner(db, audit, lambda _job: calls.append(1))
    job_id = make_job(db, tenant)

    def deliver() -> None:
        gate.wait()
        runner.run(tenant, job_id)

    threads = [threading.Thread(target=deliver) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1


def test_failure_schedules_retry_then_succeeds(
    db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    attempts: list[int] = []

    def flaky(_job: JobContext) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("temporary")

    runner = make_runner(db, audit, flaky)
    job_id = make_job(db, tenant)
    assert runner.run(tenant, job_id) == "retry"
    state = job_state(db, tenant, job_id)
    assert state.status == "queued"
    assert state.last_error == "ValueError: temporary"
    assert runner.run(tenant, job_id) == "succeeded"


def test_exhausted_job_goes_dead_and_is_audited(
    db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    def always_fails(_job: JobContext) -> None:
        raise ValueError("permanent")

    runner = make_runner(db, audit, always_fails)
    job_id = make_job(db, tenant, max_attempts=2)
    assert [runner.run(tenant, job_id) for _ in range(2)] == ["retry", "dead"]
    with db.tenant_session(tenant) as session:
        assert audit.verify(session, tenant).checked == 1


def test_unknown_job_type_fails_visibly(db: Database, audit: AuditLog, tenant: uuid.UUID) -> None:
    runner = make_runner(db, audit, lambda _job: None)
    job_id = make_job(db, tenant, job_type="unregistered", max_attempts=1)
    assert runner.run(tenant, job_id) == "dead"
    assert "no handler" in (job_state(db, tenant, job_id).last_error or "")


def test_crashed_worker_is_recovered_and_rerun(
    db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    job_id = make_job(db, tenant)
    set_job(
        db, tenant, job_id, status="running", attempts=1, heartbeat_at=utcnow() - timedelta(hours=1)
    )
    Maintenance(db, audit, heartbeat_timeout_seconds=60).recover_stalled()
    state = job_state(db, tenant, job_id)
    assert state.status == "queued"
    assert state.last_error == "worker heartbeat expired"
    assert make_runner(db, audit, lambda _job: None).run(tenant, job_id) == "succeeded"


def test_healthy_worker_is_left_alone(db: Database, audit: AuditLog, tenant: uuid.UUID) -> None:
    job_id = make_job(db, tenant)
    set_job(db, tenant, job_id, status="running", attempts=1, heartbeat_at=utcnow())
    Maintenance(db, audit, heartbeat_timeout_seconds=60).recover_stalled()
    assert job_state(db, tenant, job_id).status == "running"


def test_stalled_job_past_retry_limit_goes_dead(
    db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    job_id = make_job(db, tenant, max_attempts=1)
    set_job(
        db, tenant, job_id, status="running", attempts=1, heartbeat_at=utcnow() - timedelta(hours=1)
    )
    Maintenance(db, audit, heartbeat_timeout_seconds=60).recover_stalled()
    assert job_state(db, tenant, job_id).status == "dead"


def test_late_worker_is_fenced_out(db: Database, audit: AuditLog, tenant: uuid.UUID) -> None:
    """A worker presumed dead wakes after recovery. Its result must not overwrite the newer run."""
    maintenance = Maintenance(db, audit, heartbeat_timeout_seconds=60)
    outcome: list[str] = []

    def slow(_job: JobContext) -> None:
        set_job(db, tenant, job_id, heartbeat_at=utcnow() - timedelta(hours=1))
        maintenance.recover_stalled()
        set_job(db, tenant, job_id, status="running", attempts=2, heartbeat_at=utcnow())

    job_id = make_job(db, tenant)
    outcome.append(make_runner(db, audit, slow).run(tenant, job_id))
    assert outcome == ["fenced"]
    state = job_state(db, tenant, job_id)
    assert (state.status, state.attempts) == ("running", 2)


def test_lost_broker_message_is_redispatched(
    db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    job_id = make_job(db, tenant)
    set_job(db, tenant, job_id, dispatched_at=utcnow() - timedelta(minutes=10))
    quiet_other_tenants(db, tenant)
    Maintenance(db, audit, 60).redispatch_queued()
    with db.tenant_session(tenant) as session:
        assert len(session.scalars(select(Outbox)).all()) == 2


def test_due_retry_is_redispatched_only_when_due(
    db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    job_id = make_job(db, tenant)
    set_job(db, tenant, job_id, dispatched_at=None, next_run_at=utcnow() + timedelta(hours=1))
    quiet_other_tenants(db, tenant)
    Maintenance(db, audit, 60).redispatch_queued()
    with db.tenant_session(tenant) as session:
        assert len(session.scalars(select(Outbox)).all()) == 1


def test_due_timer_becomes_exactly_one_job(
    db: Database, audit: AuditLog, tenant: uuid.UUID
) -> None:
    with db.tenant_session(tenant) as session:
        session.add(
            Timer(
                tenant_id=tenant,
                kind="sla.escalate",
                idempotency_key="sla-1",
                fire_at=utcnow() - timedelta(seconds=1),
                payload={"task": "a"},
            )
        )
        session.add(
            Timer(
                tenant_id=tenant,
                kind="sla.later",
                idempotency_key="sla-2",
                fire_at=utcnow() + timedelta(hours=1),
            )
        )
    maintenance = Maintenance(db, audit, 60)
    maintenance.fire_timers()
    maintenance.fire_timers()
    with db.tenant_session(tenant) as session:
        jobs = session.scalars(select(ProcessingJob)).all()
    assert [(job.type, job.payload) for job in jobs] == [("sla.escalate", {"task": "a"})]


def test_job_of_another_tenant_cannot_be_claimed(
    db: Database, audit: AuditLog, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    job_id = make_job(db, tenant)
    assert make_runner(db, audit, lambda _job: None).run(other_tenant, job_id) == "skipped"
    assert job_state(db, tenant, job_id).status == "queued"
