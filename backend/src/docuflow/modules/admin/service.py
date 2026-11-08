"""What an operator needs to see and do: usage, health and the job queue (F19).

Rules that hold throughout:
- Everything here needs the ``admin.operate`` system right.
- Analytics are counts for the whole organization, never a ranking or a trail of one person's
  activity. Document titles, field values and job payloads do not appear in them.
- Job errors are shown cut short, because an error can quote text from a file.
- Retrying a job is the only change an operator can make here. It starts a fresh round of
  attempts for a job that has given up, and is audited with who did it and what had gone wrong.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, cast, distinct, func, select, text
from sqlalchemy.orm import Session

from docuflow.modules.collaboration.models import Task
from docuflow.modules.compression.models import Derivative
from docuflow.modules.documents.models import BinaryObject, DocumentVersion
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.models import User
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.indexing.models import DocumentType
from docuflow.modules.repository.models import Cabinet, Document
from docuflow.modules.retention.models import DisposalEvent, LegalHold, LegalHoldItem
from docuflow.modules.workflow.models import WorkflowInstance
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import utcnow
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound
from docuflow.platform.jobs import WORKER_HEARTBEAT_KEY, requeue
from docuflow.platform.models import AuditCheckpoint, AuditEvent, ProcessingJob

OPERATE = "admin.operate"
MAX_DAYS = 365
ERROR_LIMIT = 300
BACKLOG_AFTER = timedelta(minutes=5)
CHECKPOINT_STALE_AFTER = timedelta(hours=48)
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "dead")
RETRYABLE = ("dead", "failed")


@dataclass(frozen=True)
class DayCount:
    day: date
    count: int


@dataclass(frozen=True)
class NamedCount:
    name: str
    count: int
    bytes: int = 0


@dataclass(frozen=True)
class Analytics:
    days: int
    documents: int
    archived_documents: int
    versions: int
    active_users: int
    disabled_users: int
    stored_bytes: int
    copy_bytes: int
    untyped_documents: int
    by_type: list[NamedCount]
    by_cabinet: list[NamedCount]
    text_states: dict[str, int]
    open_tasks: int
    overdue_tasks: int
    workflows: dict[str, int]
    documents_on_hold: int
    disposals_in_period: int
    uploads: list[DayCount]
    searches: list[DayCount]
    questions: list[DayCount]


@dataclass(frozen=True)
class JobRow:
    job: ProcessingJob
    error: str | None


@dataclass(frozen=True)
class JobSummary:
    counts: dict[str, int]
    queued_oldest_seconds: int | None
    waiting_types: list[NamedCount]
    dead_types: list[NamedCount]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


class AdminService:
    def __init__(self, audit: AuditLog, policy: PolicyService) -> None:
        self._audit = audit
        self._policy = policy

    def _require(self, session: Session, principal: Principal) -> None:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        if OPERATE not in held:
            raise Forbidden("You do not have permission to do that.", details={"required": OPERATE})

    # Usage

    @staticmethod
    def _series(rows: list[tuple[Any, int]], start: date, days: int) -> list[DayCount]:
        """A count for every day in the period, with zeros where nothing happened."""
        found = {(r[0].date() if isinstance(r[0], datetime) else r[0]): int(r[1]) for r in rows}
        return [
            DayCount(start + timedelta(days=i), found.get(start + timedelta(days=i), 0))
            for i in range(days)
        ]

    def analytics(self, session: Session, principal: Principal, days: int = 30) -> Analytics:
        self._require(session, principal)
        if not 1 <= days <= MAX_DAYS:
            raise DomainError(f"Choose a period of 1 to {MAX_DAYS} days.")
        now = utcnow()
        start_day = (now - timedelta(days=days - 1)).date()
        since = datetime.combine(start_day, datetime.min.time(), tzinfo=now.tzinfo)

        def count(statement: Any) -> int:
            return int(session.scalar(statement) or 0)

        def per_day(column: Any, *where: Any) -> list[DayCount]:
            day = cast(column, Date)
            rows = session.execute(
                select(day, func.count()).where(column >= since, *where).group_by(day)
            ).all()
            return self._series([(r[0], r[1]) for r in rows], start_day, days)

        def named(statement: Any) -> list[NamedCount]:
            return [
                NamedCount(str(n), int(c), int(b or 0)) for n, c, b in session.execute(statement)
            ]

        active = Document.status == "active"
        by_type = named(
            select(DocumentType.name, func.count(Document.id), func.sum(Document.size_bytes))
            .join(Document, Document.document_type_id == DocumentType.id)
            .where(active)
            .group_by(DocumentType.name)
            .order_by(func.count(Document.id).desc(), DocumentType.name)
            .limit(20)
        )
        by_cabinet = named(
            select(Cabinet.name, func.count(Document.id), func.sum(Document.size_bytes))
            .join(Document, Document.cabinet_id == Cabinet.id, isouter=True)
            .group_by(Cabinet.name)
            .order_by(func.sum(Document.size_bytes).desc().nulls_last(), Cabinet.name)
            .limit(50)
        )
        states = dict(
            session.execute(
                select(DocumentVersion.text_state, func.count()).group_by(
                    DocumentVersion.text_state
                )
            )
            .tuples()
            .all()
        )
        workflows = dict(
            session.execute(
                select(WorkflowInstance.status, func.count()).group_by(WorkflowInstance.status)
            )
            .tuples()
            .all()
        )
        return Analytics(
            days=days,
            documents=count(select(func.count()).select_from(Document).where(active)),
            archived_documents=count(
                select(func.count()).select_from(Document).where(Document.status == "archived")
            ),
            versions=count(select(func.count()).select_from(DocumentVersion)),
            active_users=count(
                select(func.count())
                .select_from(User)
                .where(User.status == "active", User.kind == "person")
            ),
            disabled_users=count(
                select(func.count()).select_from(User).where(User.status == "disabled")
            ),
            stored_bytes=count(select(func.coalesce(func.sum(BinaryObject.size_bytes), 0))),
            copy_bytes=count(
                select(func.coalesce(func.sum(Derivative.derivative_size), 0)).where(
                    Derivative.binary_object_id.is_not(None)
                )
            ),
            untyped_documents=count(
                select(func.count())
                .select_from(Document)
                .where(active, Document.document_type_id.is_(None))
            ),
            by_type=by_type,
            by_cabinet=by_cabinet,
            text_states={k: int(v) for k, v in states.items()},
            open_tasks=count(select(func.count()).select_from(Task).where(Task.status == "open")),
            overdue_tasks=count(
                select(func.count())
                .select_from(Task)
                .where(Task.status == "open", Task.due_at.is_not(None), Task.due_at < now)
            ),
            workflows={k: int(v) for k, v in workflows.items()},
            documents_on_hold=count(
                select(func.count(distinct(LegalHoldItem.document_id)))
                .join(LegalHold, LegalHold.id == LegalHoldItem.hold_id)
                .where(LegalHold.status == "active")
            ),
            disposals_in_period=count(
                select(func.count())
                .select_from(DisposalEvent)
                .where(DisposalEvent.created_at >= since)
            ),
            uploads=per_day(Document.created_at),
            searches=per_day(AuditEvent.occurred_at, AuditEvent.action == "search.performed"),
            questions=per_day(AuditEvent.occurred_at, AuditEvent.action == "assistant.asked"),
        )

    # Health

    def health(
        self,
        session: Session,
        principal: Principal,
        *,
        queue: Callable[[], Any] | None,
        storage: Callable[[], Any] | None,
        workers: Callable[[], bool] | None,
    ) -> list[Check]:
        """Whether the parts work, and whether work is piling up."""
        self._require(session, principal)
        checks: list[Check] = []

        def probe(name: str, run: Callable[[], Any] | None, detail_ok: str) -> None:
            if run is None:
                checks.append(Check(name, False, "Not checked here."))
                return
            try:
                result = run()
                ok = result is not False
                checks.append(Check(name, ok, detail_ok if ok else "Not responding."))
            except Exception:  # a failing part is the answer, not a reason to fail the page
                checks.append(Check(name, False, "Not responding."))

        probe("database", lambda: session.execute(text("SELECT 1")), "Responding.")
        probe("queue", queue, "Responding.")
        probe("storage", storage, "Responding.")
        probe("workers", workers, "A worker is running.")

        now = utcnow()
        waiting = int(
            session.scalar(
                select(func.count())
                .select_from(ProcessingJob)
                .where(
                    ProcessingJob.status == "queued",
                    ProcessingJob.next_run_at < now - BACKLOG_AFTER,
                )
            )
            or 0
        )
        checks.append(
            Check(
                "job_backlog",
                waiting == 0,
                "Nothing is waiting long."
                if waiting == 0
                else f"{waiting} jobs have waited over 5 minutes.",
            )
        )
        dead = int(
            session.scalar(
                select(func.count())
                .select_from(ProcessingJob)
                .where(ProcessingJob.status == "dead")
            )
            or 0
        )
        checks.append(
            Check(
                "dead_jobs",
                dead == 0,
                "No job has given up." if dead == 0 else f"{dead} jobs have given up.",
            )
        )
        last = session.scalar(select(func.max(AuditCheckpoint.created_at)))
        fresh = last is not None and now - last < CHECKPOINT_STALE_AFTER
        checks.append(
            Check(
                "audit_checkpoint",
                fresh,
                "The audit trail was anchored recently."
                if fresh
                else "The audit trail has not been anchored in the last 48 hours.",
            )
        )
        return checks

    # Jobs

    def job_summary(self, session: Session, principal: Principal) -> JobSummary:
        self._require(session, principal)
        counts = dict.fromkeys(JOB_STATUSES, 0)
        for status, total in session.execute(
            select(ProcessingJob.status, func.count()).group_by(ProcessingJob.status)
        ).tuples():
            counts[status] = int(total)
        oldest = session.scalar(
            select(func.min(ProcessingJob.next_run_at)).where(ProcessingJob.status == "queued")
        )

        def by_type(status: str) -> list[NamedCount]:
            return [
                NamedCount(t, int(c))
                for t, c in session.execute(
                    select(ProcessingJob.type, func.count())
                    .where(ProcessingJob.status == status)
                    .group_by(ProcessingJob.type)
                    .order_by(func.count().desc(), ProcessingJob.type)
                    .limit(20)
                )
            ]

        age = int((utcnow() - oldest).total_seconds()) if oldest is not None else None
        return JobSummary(
            counts, max(0, age) if age is not None else None, by_type("queued"), by_type("dead")
        )

    def jobs(
        self,
        session: Session,
        principal: Principal,
        *,
        status: str | None,
        job_type: str | None,
        limit: int,
        offset: int,
    ) -> list[JobRow]:
        self._require(session, principal)
        if status is not None and status not in JOB_STATUSES:
            raise DomainError("The status must be one of: " + ", ".join(JOB_STATUSES) + ".")
        query = select(ProcessingJob).order_by(ProcessingJob.created_at.desc(), ProcessingJob.id)
        if status is not None:
            query = query.where(ProcessingJob.status == status)
        if job_type:
            query = query.where(ProcessingJob.type == job_type)
        rows = session.scalars(query.limit(max(1, min(limit, 200))).offset(max(0, offset)))
        return [JobRow(job, (job.last_error or "")[:ERROR_LIMIT] or None) for job in rows]

    def retry(self, session: Session, principal: Principal, job_id: uuid.UUID) -> ProcessingJob:
        self._require(session, principal)
        job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()
        )
        if job is None:
            raise NotFound("Job not found.")
        if job.status not in RETRYABLE:
            raise Conflict("Only a job that has failed or given up can be retried.")
        before = {
            "status": job.status,
            "attempts": job.attempts,
            "error": (job.last_error or "")[:200],
        }
        requeue(session, job)
        self._audit.record(
            session,
            principal.tenant_id,
            "job.retried",
            actor=AuditActor(
                principal.user_id, "user" if principal.kind == "session" else "api_key"
            ),
            object_type="processing_job",
            object_id=str(job.id),
            details={"type": job.type, "before": before},
        )
        return job

    @staticmethod
    def worker_check(redis: Any) -> Callable[[], bool]:
        return lambda: bool(redis.exists(WORKER_HEARTBEAT_KEY))
