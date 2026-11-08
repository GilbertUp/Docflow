"""F19 over HTTP: what an operator sees of usage, health and the job queue."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database, utcnow
from docuflow.platform.jobs import HandlerRegistry, JobRunner
from docuflow.platform.models import Outbox, ProcessingJob
from tests.documents.files import text_pdf
from tests.documents.test_upload import make, put
from tests.identity.support import assign_role, audit_events, new_role, new_user
from tests.repository.support import boss_of
from tests.test_jobs import make_job, set_job
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider, store: MemoryObjectStore) -> FastAPI:
    return make_app(db, audit, keys, store, max_upload_bytes=1024 * 1024)


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss = boss_of(db, tenant)
    role = new_role(db, tenant, "Operator", ("admin.operate",))
    assign_role(db, tenant, role, user_id=boss.user_id)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Invoices")
    return {"client": client, "csrf": csrf, "folder": folder, "cabinet": cabinet}


def upload(office: dict[str, Any], name: str, text: str) -> dict[str, Any]:
    response = put(office["client"], office["csrf"], office["folder"]["id"], name, text_pdf([text]))
    assert response.status_code == 201, response.text
    return response.json()["document"]


def test_only_someone_who_may_operate_the_system_sees_any_of_it(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "clerk")
    clerk, csrf = sign_in(app, db, tenant, "clerk")
    for path in ("/admin/analytics", "/admin/health", "/admin/jobs", "/admin/jobs/summary"):
        assert clerk.get(f"{API}{path}").status_code == 403, path
    job = make_job(db, tenant)
    assert clerk.post(f"{API}/admin/jobs/{job}/retry", headers=csrf).status_code == 403


# Usage


def test_usage_counts_are_right_and_every_day_of_the_period_is_there(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    upload(office, "a.pdf", "First document text")
    upload(office, "b.pdf", "Second document text")
    upload(office, "same.pdf", "Identical content")
    upload(office, "same-again.pdf", "Identical content")  # shares its stored file
    new_user(db, tenant, "clerk")
    client = office["client"]
    client.get(f"{API}/search", params={"q": "document"})
    client.get(f"{API}/search", params={"q": "nothing"})

    data = client.get(f"{API}/admin/analytics", params={"days": 7}).json()
    assert data["documents"] == 4
    assert data["versions"] == 4
    assert data["active_users"] == 2  # the operator and the clerk
    assert data["untyped_documents"] == 4
    assert data["text_states"] == {"pending": 4}
    assert data["by_cabinet"][0]["name"] == "Finance"
    assert data["by_cabinet"][0]["count"] == 4
    assert data["by_type"] == []

    assert len(data["uploads"]) == 7
    assert [d["count"] for d in data["uploads"]][:-1] == [0] * 6  # quiet days are zeros
    assert data["uploads"][-1]["count"] == 4
    assert data["searches"][-1]["count"] == 2
    assert data["questions"][-1]["count"] == 0
    # A file shared by two documents is stored, and so counted, once.
    assert 0 < data["stored_bytes"] < sum(c["bytes"] for c in data["by_cabinet"])


def test_tasks_and_workflows_are_counted(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = upload(office, "a.pdf", "A document")["id"]
    client, csrf = office["client"], office["csrf"]
    me = client.get(f"{API}/auth/me").json()["user"]["id"]
    late = (utcnow() - timedelta(days=2)).isoformat()
    client.post(
        f"{API}/documents/{doc}/tasks",
        json={"title": "Late", "assignee_id": me, "due_at": late},
        headers=csrf,
    )
    client.post(
        f"{API}/documents/{doc}/tasks", json={"title": "Fine", "assignee_id": me}, headers=csrf
    )
    data = client.get(f"{API}/admin/analytics").json()
    assert (data["open_tasks"], data["overdue_tasks"]) == (2, 1)
    assert data["workflows"] == {}


def test_usage_never_names_a_document_a_person_or_a_job_payload(
    office: dict[str, Any],
) -> None:
    upload(office, "secret-merger-plan.pdf", "Very private words")
    text = office["client"].get(f"{API}/admin/analytics").text
    assert "secret-merger-plan" not in text
    assert "private words" not in text
    assert "boss@" not in text


def test_the_period_must_be_sensible(office: dict[str, Any]) -> None:
    client = office["client"]
    assert client.get(f"{API}/admin/analytics", params={"days": 0}).status_code == 422
    assert client.get(f"{API}/admin/analytics", params={"days": 366}).status_code == 422
    assert client.get(f"{API}/admin/analytics", params={"days": 365}).status_code == 200


# Health


def test_health_reports_each_part_and_whether_work_is_piling_up(
    app: FastAPI,
    office: dict[str, Any],
    db: Database,
    tenant: uuid.UUID,
    audit: AuditLog,
    store: MemoryObjectStore,
) -> None:
    def checks() -> dict[str, dict[str, Any]]:
        body = office["client"].get(f"{API}/admin/health").json()
        return {c["name"]: c for c in body["checks"]} | {"_ok": body["ok"]}

    first = checks()
    assert all(first[n]["ok"] for n in ("database", "queue", "storage", "workers"))
    assert first["job_backlog"]["ok"]
    assert first["dead_jobs"]["ok"]
    assert first["audit_checkpoint"]["ok"] is False  # nothing has been anchored yet
    assert first["_ok"] is False

    with db.tenant_session(tenant) as session:
        audit.write_checkpoint(session, tenant, store)
    assert checks()["audit_checkpoint"]["ok"] is True

    stuck = make_job(db, tenant)
    set_job(db, tenant, stuck, next_run_at=utcnow() - timedelta(minutes=10))
    waiting = checks()
    assert waiting["job_backlog"]["ok"] is False
    assert "1 jobs have waited" in waiting["job_backlog"]["detail"]

    dead = make_job(db, tenant)
    set_job(db, tenant, dead, status="dead")
    assert checks()["dead_jobs"]["ok"] is False
    assert checks()["_ok"] is False


# Jobs


def test_the_job_list_shows_status_and_cut_down_errors_but_never_payloads(
    office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client = office["client"]
    with db.tenant_session(tenant) as session:
        from docuflow.platform.jobs import enqueue

        job_id, _ = enqueue(
            session, tenant, "t.sensitive", "k1", payload={"secret": "do-not-show-this"}
        )
    set_job(db, tenant, job_id, status="dead", attempts=5, last_error="boom " * 200)
    ok = make_job(db, tenant, "t.fine")
    set_job(db, tenant, ok, status="succeeded")

    everything = client.get(f"{API}/admin/jobs").json()
    assert {j["type"] for j in everything} == {"t.sensitive", "t.fine"}
    dead = client.get(f"{API}/admin/jobs", params={"status": "dead"}).json()
    assert [j["type"] for j in dead] == ["t.sensitive"]
    assert len(dead[0]["error"]) == 300
    assert "do-not-show-this" not in client.get(f"{API}/admin/jobs").text
    by_type = client.get(f"{API}/admin/jobs", params={"type": "t.fine"}).json()
    assert [j["status"] for j in by_type] == ["succeeded"]
    assert client.get(f"{API}/admin/jobs", params={"status": "nonsense"}).status_code == 400


def test_the_summary_counts_jobs_by_status_and_says_how_long_the_oldest_has_waited(
    office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    old = make_job(db, tenant, "t.slow")
    set_job(db, tenant, old, next_run_at=utcnow() - timedelta(seconds=120))
    make_job(db, tenant, "t.slow")
    dead = make_job(db, tenant, "t.broken")
    set_job(db, tenant, dead, status="dead")
    summary = office["client"].get(f"{API}/admin/jobs/summary").json()
    assert summary["counts"]["queued"] == 2
    assert summary["counts"]["dead"] == 1
    assert summary["counts"]["succeeded"] == 0
    assert summary["queued_oldest_seconds"] >= 120
    assert [(t["name"], t["count"]) for t in summary["waiting_types"]] == [("t.slow", 2)]
    assert [(t["name"], t["count"]) for t in summary["dead_types"]] == [("t.broken", 1)]


def test_a_job_that_gave_up_can_be_retried_and_then_really_runs(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf = office["client"], office["csrf"]
    job = make_job(db, tenant, "t.retry")
    set_job(db, tenant, job, status="dead", attempts=5, last_error="it broke", finished_at=utcnow())
    with db.tenant_session(tenant) as session:
        before = len(session.scalars(select(Outbox)).all())

    done = client.post(f"{API}/admin/jobs/{job}/retry", headers=csrf)
    assert done.status_code == 200, done.text
    assert (done.json()["status"], done.json()["attempts"]) == ("queued", 0)
    assert done.json()["finished_at"] is None
    with db.tenant_session(tenant) as session:
        assert len(session.scalars(select(Outbox)).all()) == before + 1  # sent to a worker again
    (event,) = audit_events(db, tenant, "job.retried")
    assert event.details["before"]["status"] == "dead"
    assert event.details["before"]["error"] == "it broke"

    registry = HandlerRegistry()
    registry.register("t.retry")(lambda _job: None)
    result = JobRunner(db, registry, app.state.text._audit, 0).run(tenant, job)
    assert result == "succeeded"
    assert client.get(f"{API}/admin/jobs", params={"status": "succeeded"}).json()[0]["id"] == str(
        job
    )


def test_only_a_job_that_failed_or_gave_up_can_be_retried(
    office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client, csrf = office["client"], office["csrf"]
    for status in ("queued", "running", "succeeded"):
        job = make_job(db, tenant, f"t.{status}")
        set_job(db, tenant, job, status=status)
        refused = client.post(f"{API}/admin/jobs/{job}/retry", headers=csrf)
        assert refused.status_code == 409, status
    failed = make_job(db, tenant, "t.failed")
    set_job(db, tenant, failed, status="failed")
    assert client.post(f"{API}/admin/jobs/{failed}/retry", headers=csrf).status_code == 200
    assert client.post(f"{API}/admin/jobs/{uuid.uuid4()}/retry", headers=csrf).status_code == 404


def test_one_organization_cannot_see_or_retry_another_ones_jobs(
    office: dict[str, Any], db: Database, other_tenant: uuid.UUID
) -> None:
    theirs = make_job(db, other_tenant, "t.theirs")
    set_job(db, other_tenant, theirs, status="dead")
    assert office["client"].get(f"{API}/admin/jobs").json() == []
    refused = office["client"].post(f"{API}/admin/jobs/{theirs}/retry", headers=office["csrf"])
    assert refused.status_code == 404
    with db.tenant_session(other_tenant) as session:
        status = session.scalar(select(ProcessingJob.status).where(ProcessingJob.id == theirs))
        assert status == "dead"
