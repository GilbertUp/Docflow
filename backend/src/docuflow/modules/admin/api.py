"""Operations over HTTP: usage, health and the job queue."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from docuflow.modules.admin.service import AdminService, Analytics, DayCount, NamedCount
from docuflow.modules.identity.api import Ctx, User_

router = APIRouter(prefix="/api/v1", tags=["admin"])


def get_admin(request: Request) -> AdminService:
    service: AdminService = request.app.state.admin
    return service


Adm = Annotated[AdminService, Depends(get_admin)]


class DayOut(BaseModel):
    day: date
    count: int


class CountOut(BaseModel):
    name: str
    count: int
    bytes: int


class AnalyticsOut(BaseModel):
    days: int
    documents: int
    archived_documents: int
    versions: int
    active_users: int
    disabled_users: int
    # Stored files, once each however many documents share them. Smaller copies are separate.
    stored_bytes: int
    copy_bytes: int
    untyped_documents: int
    by_type: list[CountOut]
    by_cabinet: list[CountOut]
    text_states: dict[str, int]
    open_tasks: int
    overdue_tasks: int
    workflows: dict[str, int]
    documents_on_hold: int
    disposals_in_period: int
    uploads: list[DayOut]
    searches: list[DayOut]
    questions: list[DayOut]


class CheckOut(BaseModel):
    name: str
    ok: bool
    detail: str


class HealthOut(BaseModel):
    ok: bool
    checks: list[CheckOut]


class JobOut(BaseModel):
    id: uuid.UUID
    type: str
    queue: str
    status: str
    attempts: int
    max_attempts: int
    progress: int
    # Cut short, because an error can quote text from a file.
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class JobSummaryOut(BaseModel):
    counts: dict[str, int]
    queued_oldest_seconds: int | None
    waiting_types: list[CountOut]
    dead_types: list[CountOut]


def _days(items: list[DayCount]) -> list[DayOut]:
    return [DayOut(day=i.day, count=i.count) for i in items]


def _counts(items: list[NamedCount]) -> list[CountOut]:
    return [CountOut(name=i.name, count=i.count, bytes=i.bytes) for i in items]


def _analytics(a: Analytics) -> AnalyticsOut:
    return AnalyticsOut(
        days=a.days,
        documents=a.documents,
        archived_documents=a.archived_documents,
        versions=a.versions,
        active_users=a.active_users,
        disabled_users=a.disabled_users,
        stored_bytes=a.stored_bytes,
        copy_bytes=a.copy_bytes,
        untyped_documents=a.untyped_documents,
        by_type=_counts(a.by_type),
        by_cabinet=_counts(a.by_cabinet),
        text_states=a.text_states,
        open_tasks=a.open_tasks,
        overdue_tasks=a.overdue_tasks,
        workflows=a.workflows,
        documents_on_hold=a.documents_on_hold,
        disposals_in_period=a.disposals_in_period,
        uploads=_days(a.uploads),
        searches=_days(a.searches),
        questions=_days(a.questions),
    )


@router.get("/admin/analytics")
def analytics(
    ctx: Ctx, adm: Adm, who: User_, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> AnalyticsOut:
    """Counts for the whole organization. Needs admin.operate."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _analytics(adm.analytics(session, who, days))


@router.get("/admin/health")
def health(ctx: Ctx, adm: Adm, who: User_) -> HealthOut:
    """Whether the parts respond and whether work is piling up."""
    services = ctx.services
    redis = getattr(services, "redis", None)
    store = getattr(services, "store", None)
    with services.db.tenant_session(who.tenant_id) as session:
        checks = adm.health(
            session,
            who,
            queue=redis.ping if redis is not None else None,
            storage=store.check if store is not None else None,
            workers=adm.worker_check(redis) if redis is not None else None,
        )
    return HealthOut(
        ok=all(c.ok for c in checks),
        checks=[CheckOut(name=c.name, ok=c.ok, detail=c.detail) for c in checks],
    )


@router.get("/admin/jobs/summary")
def job_summary(ctx: Ctx, adm: Adm, who: User_) -> JobSummaryOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        s = adm.job_summary(session, who)
        return JobSummaryOut(
            counts=s.counts,
            queued_oldest_seconds=s.queued_oldest_seconds,
            waiting_types=_counts(s.waiting_types),
            dead_types=_counts(s.dead_types),
        )


@router.get("/admin/jobs")
def list_jobs(
    ctx: Ctx,
    adm: Adm,
    who: User_,
    status: Annotated[str | None, Query(max_length=20)] = None,
    type: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> list[JobOut]:
    """Jobs, newest first. Payloads are never shown."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            JobOut(
                id=row.job.id,
                type=row.job.type,
                queue=row.job.queue,
                status=row.job.status,
                attempts=row.job.attempts,
                max_attempts=row.job.max_attempts,
                progress=row.job.progress,
                error=row.error,
                created_at=row.job.created_at,
                finished_at=row.job.finished_at,
            )
            for row in adm.jobs(
                session, who, status=status, job_type=type, limit=limit, offset=offset
            )
        ]


@router.post("/admin/jobs/{job_id}/retry")
def retry_job(job_id: uuid.UUID, ctx: Ctx, adm: Adm, who: User_) -> JobOut:
    """Starts a fresh round of attempts for a job that failed or gave up."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        job = adm.retry(session, who, job_id)
        return JobOut(
            id=job.id,
            type=job.type,
            queue=job.queue,
            status=job.status,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            progress=job.progress,
            error=(job.last_error or "")[:300] or None,
            created_at=job.created_at,
            finished_at=job.finished_at,
        )
