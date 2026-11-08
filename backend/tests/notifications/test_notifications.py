"""F13: email preferences and delivery, and deadlines kept by timers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select

from docuflow.modules.identity.models import AclEntry
from docuflow.modules.identity.policy import bump_permission_epoch
from docuflow.modules.notifications.models import EmailMessage
from docuflow.modules.notifications.service import (
    EMAIL_JOB,
    SLA_JOB,
    NotificationService,
)
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from docuflow.platform.jobs import JobContext
from docuflow.platform.models import ProcessingJob, Timer
from tests.documents.files import text_pdf
from tests.documents.test_upload import make, put
from tests.identity.support import assign_role, audit_events, new_role, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db


class FakeSender:
    def __init__(self, fail: int = 0) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fail = fail

    def send(self, to_address: str, subject: str, body: str) -> None:
        if self.fail > 0:
            self.fail -= 1
            raise ConnectionError("mail server down")
        self.sent.append((to_address, subject, body))


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    return make_app(db, audit, keys, MemoryObjectStore(), max_upload_bytes=1024 * 1024)


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss = boss_of(db, tenant)
    role = new_role(db, tenant, "Flow admin", ("workflows.manage",))
    assign_role(db, tenant, role, user_id=boss.user_id)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Invoices")
    document = put(client, csrf, folder["id"], "invoice.pdf", text_pdf(["An invoice"])).json()[
        "document"
    ]
    return {
        "id": str(boss.user_id),
        "client": client,
        "csrf": csrf,
        "folder": folder,
        "document": document,
    }


def person(
    app: FastAPI, db: Database, tenant: uuid.UUID, office: dict[str, Any], name: str
) -> dict[str, Any]:
    user_id = new_user(db, tenant, name)
    allow(db, tenant, uuid.UUID(office["folder"]["acl_anchor_id"]), user_id, "read")
    client, csrf = sign_in(app, db, tenant, name)
    return {"id": str(user_id), "client": client, "csrf": csrf}


def give_task(office: dict[str, Any], assignee: str, **extra: Any):
    return office["client"].post(
        f"{API}/documents/{office['document']['id']}/tasks",
        json={"title": "Check the total", "assignee_id": assignee, **extra},
        headers=office["csrf"],
    )


def job(tenant: uuid.UUID, payload: dict[str, Any]) -> JobContext:
    return JobContext(tenant, uuid.uuid4(), payload, lambda _percent: None)


def timers(db: Database, tenant: uuid.UUID) -> list[Timer]:
    with db.tenant_session(tenant) as session:
        return list(session.scalars(select(Timer).where(Timer.kind == SLA_JOB)))


def notes(who: dict[str, Any]) -> list[dict[str, Any]]:
    return who["client"].get(f"{API}/notifications").json()["items"]


def service(app: FastAPI, sender: FakeSender | None = None) -> NotificationService:
    base: NotificationService = app.state.notification_settings
    return NotificationService(base._audit, base._policy, sender)


# Preferences and email


def test_email_is_opt_in_per_kind_and_unknown_kinds_are_refused(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    clerk = person(app, db, tenant, office, "clerk")
    first = clerk["client"].get(f"{API}/notification-preferences").json()
    assert {item["email"] for item in first} == {False}
    changed = clerk["client"].put(
        f"{API}/notification-preferences",
        json={"email": {"task_assigned": True}},
        headers=clerk["csrf"],
    )
    assert changed.status_code == 200
    assert {item["kind"]: item["email"] for item in changed.json()}["task_assigned"] is True
    bad = clerk["client"].put(
        f"{API}/notification-preferences", json={"email": {"gossip": True}}, headers=clerk["csrf"]
    )
    assert bad.status_code == 400


def test_a_notification_is_emailed_only_when_asked_for_and_the_result_is_tracked(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    clerk = person(app, db, tenant, office, "clerk")
    other = person(app, db, tenant, office, "other")
    clerk["client"].put(
        f"{API}/notification-preferences",
        json={"email": {"task_assigned": True}},
        headers=clerk["csrf"],
    )
    assert give_task(office, clerk["id"]).status_code == 201
    assert give_task(office, other["id"]).status_code == 201

    with db.tenant_session(tenant) as session:
        mails = list(session.scalars(select(EmailMessage)))
        queued = list(session.scalars(select(ProcessingJob).where(ProcessingJob.type == EMAIL_JOB)))
    assert len(mails) == 1
    assert len(queued) == 1
    assert mails[0].to_address.startswith("clerk")
    assert "Check the total" in mails[0].subject
    assert "invoice" not in mails[0].body.lower()  # titles only, never document text

    sender = FakeSender()
    service(app, sender).run_email(db, job(tenant, {"email_id": str(mails[0].id)}))
    assert [to for to, _s, _b in sender.sent] == [mails[0].to_address]
    delivered = clerk["client"].get(f"{API}/email-deliveries").json()
    assert (delivered[0]["status"], delivered[0]["attempts"]) == ("sent", 1)
    assert other["client"].get(f"{API}/email-deliveries").json() == []
    # Sending the same job again sends nothing more.
    service(app, sender).run_email(db, job(tenant, {"email_id": str(mails[0].id)}))
    assert len(sender.sent) == 1


def test_a_failing_mail_server_is_tried_again_and_then_given_up_on(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    clerk = person(app, db, tenant, office, "clerk")
    clerk["client"].put(
        f"{API}/notification-preferences",
        json={"email": {"task_assigned": True}},
        headers=clerk["csrf"],
    )
    give_task(office, clerk["id"])
    with db.tenant_session(tenant) as session:
        email_id = str(session.scalars(select(EmailMessage.id)).one())
    flaky = service(app, FakeSender(fail=2))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            flaky.run_email(db, job(tenant, {"email_id": email_id}))
    flaky.run_email(db, job(tenant, {"email_id": email_id}))
    row = clerk["client"].get(f"{API}/email-deliveries").json()[0]
    assert (row["status"], row["attempts"]) == ("sent", 3)

    give_task(office, clerk["id"], title="Another")
    with db.tenant_session(tenant) as session:
        second = str(
            session.scalars(select(EmailMessage.id).where(EmailMessage.status == "queued")).one()
        )
    down = service(app, FakeSender(fail=9))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            down.run_email(db, job(tenant, {"email_id": second}))
    down.run_email(db, job(tenant, {"email_id": second}))  # the third attempt ends it quietly
    states = {d["id"]: d for d in clerk["client"].get(f"{API}/email-deliveries").json()}
    assert states[second]["status"] == "failed"
    assert "mail server down" in states[second]["last_error"]


def test_without_email_set_up_it_ends_as_failed_with_the_reason(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    clerk = person(app, db, tenant, office, "clerk")
    clerk["client"].put(
        f"{API}/notification-preferences",
        json={"email": {"task_assigned": True}},
        headers=clerk["csrf"],
    )
    give_task(office, clerk["id"])
    with db.tenant_session(tenant) as session:
        email_id = str(session.scalars(select(EmailMessage.id)).one())
    service(app).run_email(db, job(tenant, {"email_id": email_id}))
    row = clerk["client"].get(f"{API}/email-deliveries").json()[0]
    assert row["status"] == "failed"
    assert "not set up" in row["last_error"]
    assert row["attempts"] == 1


# SLA policy


def test_only_someone_who_manages_workflows_can_change_the_policy(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    clerk = person(app, db, tenant, office, "clerk")
    body = {
        "reminder_before_hours": 12,
        "escalate_after_hours": 6,
        "escalate_to_user_id": office["id"],
    }
    assert (
        clerk["client"].put(f"{API}/sla-policy", json=body, headers=clerk["csrf"]).status_code
        == 403
    )
    saved = office["client"].put(f"{API}/sla-policy", json=body, headers=office["csrf"])
    assert saved.status_code == 200, saved.text
    assert saved.json()["escalate_to_name"] == "boss"
    assert clerk["client"].get(f"{API}/sla-policy").json()["reminder_before_hours"] == 12
    assert len(audit_events(db, tenant, "sla_policy.updated")) == 1
    ghost = office["client"].put(
        f"{API}/sla-policy",
        json={**body, "escalate_to_user_id": str(uuid.uuid4())},
        headers=office["csrf"],
    )
    assert ghost.status_code == 400


# Deadlines


def due_in(hours: int) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def stage_payloads(db: Database, tenant: uuid.UUID) -> dict[str, dict[str, Any]]:
    return {t.payload["stage"]: t.payload for t in timers(db, tenant)}


def test_a_task_with_a_due_time_gets_a_reminder_an_overdue_and_an_escalation_timer(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    office["client"].put(
        f"{API}/sla-policy",
        json={"reminder_before_hours": 12, "escalate_after_hours": 24, "escalate_to_user_id": None},
        headers=office["csrf"],
    )
    clerk = person(app, db, tenant, office, "clerk")
    assert give_task(office, clerk["id"]).status_code == 201
    assert timers(db, tenant) == []  # no due time, so nothing to keep
    assert give_task(office, clerk["id"], title="Due", due_at=due_in(48)).status_code == 201
    assert set(stage_payloads(db, tenant)) == {"remind", "overdue", "escalate"}


def test_the_reminder_goes_to_the_assignee_and_the_escalation_to_whoever_made_the_task(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    office["client"].put(
        f"{API}/sla-policy",
        json={"reminder_before_hours": 12, "escalate_after_hours": 24, "escalate_to_user_id": None},
        headers=office["csrf"],
    )
    clerk = person(app, db, tenant, office, "clerk")
    give_task(office, clerk["id"], due_at=due_in(48))
    payloads = stage_payloads(db, tenant)
    svc = service(app)

    svc.run_sla(db, job(tenant, payloads["remind"]))
    svc.run_sla(db, job(tenant, payloads["overdue"]))
    kinds = [item["kind"] for item in notes(clerk)]
    assert "task_reminder" in kinds
    assert "task_overdue" in kinds

    svc.run_sla(db, job(tenant, payloads["escalate"]))
    escalated = [item for item in notes(office) if item["kind"] == "task_escalated"]
    assert len(escalated) == 1
    assert "clerk" in escalated[0]["title"]
    assert len(audit_events(db, tenant, "task.escalated")) == 1


def test_stale_finished_or_unreadable_tasks_send_nothing(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    clerk = person(app, db, tenant, office, "clerk")
    made = give_task(office, clerk["id"], due_at=due_in(48)).json()
    original = stage_payloads(db, tenant)["overdue"]
    svc = service(app)

    # A new due time retires the old timers, and makes new ones.
    moved = office["client"].patch(
        f"{API}/tasks/{made['id']}",
        json={"row_version": made["row_version"], "due_at": due_in(96)},
        headers=office["csrf"],
    )
    assert moved.status_code == 200, moved.text
    svc.run_sla(db, job(tenant, original))
    assert [i for i in notes(clerk) if i["kind"] == "task_overdue"] == []
    assert len([t for t in timers(db, tenant) if t.payload["stage"] == "overdue"]) == 2

    # A finished task is left alone.
    fresh = [t.payload for t in timers(db, tenant) if t.payload["due_at"] != original["due_at"]]
    latest = next(p for p in fresh if p["stage"] == "overdue")
    done = clerk["client"].patch(
        f"{API}/tasks/{made['id']}",
        json={"row_version": moved.json()["row_version"], "status": "done"},
        headers=clerk["csrf"],
    )
    assert done.status_code == 200
    svc.run_sla(db, job(tenant, latest))
    assert [i for i in notes(clerk) if i["kind"] == "task_overdue"] == []

    # A person who can no longer read the document is not told about it.
    other = give_task(office, clerk["id"], title="Again", due_at=due_in(48)).json()
    assert other["id"] != made["id"]
    with db.tenant_session(tenant) as session:
        session.execute(delete(AclEntry).where(AclEntry.principal_id == uuid.UUID(clerk["id"])))
        bump_permission_epoch(session, tenant)
    again = next(
        t.payload
        for t in timers(db, tenant)
        if t.payload["task_id"] == other["id"] and t.payload["stage"] == "overdue"
    )
    svc.run_sla(db, job(tenant, again))
    assert [i for i in notes(clerk) if i["kind"] == "task_overdue"] == []
