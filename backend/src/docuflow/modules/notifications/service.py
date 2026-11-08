"""Telling people, in the app and by email, and keeping deadlines honest (F13).

Rules that hold throughout:
- Every notification is created through ``add_notification``, so email preferences and delivery
  tracking apply to all of them in one place.
- Email is opt-in per kind of notification. It carries the title and a pointer back to the app, and
  never document text, so a mailbox never holds more than the person was already told.
- A deadline is kept by timers that live in the database. A timer remembers the due time it was
  made for, so changing a task's due time quietly retires the old reminders.
- A reminder or escalation only goes to someone who can still read the document, and only while
  the task is still open.
- Sending is a job that is tried again when the mail server is down, and ends as failed with the
  reason when email is not set up or keeps failing. Every attempt is recorded.
"""

from __future__ import annotations

import smtplib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage as MimeMessage
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from docuflow.modules.collaboration.models import Notification, Task
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.models import User
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.notifications.models import EmailMessage, NotificationPreference, SlaPolicy
from docuflow.modules.repository.models import Document
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import Database, utcnow
from docuflow.platform.errors import DomainError, Forbidden
from docuflow.platform.jobs import JobContext, enqueue
from docuflow.platform.models import Timer

MANAGE = "workflows.manage"
SLA_JOB = "notifications.sla"
EMAIL_JOB = "notifications.send_email"
KINDS: dict[str, str] = {
    "task_assigned": "A task is given to me",
    "task_done": "A task I made is done",
    "mention": "Someone mentions me",
    "workflow_finished": "A workflow I started finishes",
    "task_reminder": "A task of mine is due soon",
    "task_overdue": "A task of mine is overdue",
    "task_escalated": "A task is escalated to me",
}
MAX_HOURS = 24 * 365
MAX_EMAIL_ATTEMPTS = 3
DEFAULT_REMINDER_HOURS = 24


class EmailNotConfigured(Exception):
    """Email is not set up on this server. Sending again would not help."""


class EmailSender(Protocol):
    def send(self, to_address: str, subject: str, body: str) -> None: ...


class NoEmail:
    def send(self, to_address: str, subject: str, body: str) -> None:
        raise EmailNotConfigured("Email is not set up on this server.")


class SmtpSender:
    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        username: str | None,
        password: str | None,
        starttls: bool,
    ) -> None:
        self._host, self._port, self._sender = host, port, sender
        self._username, self._password, self._starttls = username, password, starttls

    def send(self, to_address: str, subject: str, body: str) -> None:
        message = MimeMessage()
        message["From"] = self._sender
        message["To"] = to_address
        message["Subject"] = subject.replace("\r", " ").replace("\n", " ")
        message.set_content(body)
        with smtplib.SMTP(self._host, self._port, timeout=30) as server:
            if self._starttls:
                server.starttls()
            if self._username and self._password:
                server.login(self._username, self._password)
            server.send_message(message)


def build_sender(settings: Any) -> EmailSender:
    host = getattr(settings, "smtp_host", None)
    if not host:
        return NoEmail()
    password = getattr(settings, "smtp_password", None)
    return SmtpSender(
        host,
        getattr(settings, "smtp_port", 587),
        getattr(settings, "smtp_from", "docuflow@localhost"),
        getattr(settings, "smtp_username", None),
        password.get_secret_value() if password is not None else None,
        getattr(settings, "smtp_starttls", True),
    )


# Used by the rest of the system


def add_notification(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    title: str,
    document_id: uuid.UUID | None = None,
    ref_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> Notification:
    """Tells one person something, and queues an email when they asked for that kind by email."""
    note = Notification(
        tenant_id=tenant_id,
        user_id=user_id,
        kind=kind,
        title=title[:300],
        document_id=document_id,
        ref_id=ref_id,
        actor_id=actor_id,
    )
    session.add(note)
    session.flush()
    wants = session.scalar(
        select(NotificationPreference.email).where(
            NotificationPreference.user_id == user_id, NotificationPreference.kind == kind
        )
    )
    user = session.get(User, user_id) if wants else None
    if user is not None and user.status == "active" and user.email:
        mail = EmailMessage(
            tenant_id=tenant_id,
            user_id=user_id,
            notification_id=note.id,
            to_address=user.email,
            subject=f"DocuFlow: {note.title}"[:300],
            body=f"{note.title}\n\nOpen DocuFlow to see more.",
        )
        session.add(mail)
        session.flush()
        enqueue(
            session,
            tenant_id,
            EMAIL_JOB,
            f"email:{mail.id}",
            queue="notify",
            payload={"email_id": str(mail.id)},
        )
    return note


def sla_for(session: Session) -> tuple[int, int | None, uuid.UUID | None]:
    """The reminder hours, escalation hours and who is told, with defaults when none is set."""
    policy = session.scalar(select(SlaPolicy))
    if policy is None:
        return DEFAULT_REMINDER_HOURS, None, None
    return policy.reminder_before_hours, policy.escalate_after_hours, policy.escalate_to_user_id


def schedule_task_timers(session: Session, tenant_id: uuid.UUID, task: Task) -> None:
    """Sets the reminder, overdue and escalation timers for a task with a due time."""
    if task.due_at is None:
        return
    remind_hours, escalate_hours, _to = sla_for(session)
    due = task.due_at
    stamp = due.isoformat()
    now = utcnow()
    stages: list[tuple[str, datetime]] = []
    if remind_hours > 0:
        stages.append(("remind", due - timedelta(hours=remind_hours)))
    stages.append(("overdue", due))
    if escalate_hours is not None:
        stages.append(("escalate", due + timedelta(hours=escalate_hours)))
    for stage, when in stages:
        key = f"sla:{stage}:{task.id}:{stamp}"
        if stage == "remind" and when <= now:
            continue  # already inside the reminder window, so a reminder would only be noise
        exists = session.scalar(
            select(Timer.id).where(Timer.tenant_id == tenant_id, Timer.idempotency_key == key)
        )
        if exists is not None:
            continue
        session.add(
            Timer(
                tenant_id=tenant_id,
                kind=SLA_JOB,
                idempotency_key=key,
                fire_at=when,
                payload={"task_id": str(task.id), "stage": stage, "due_at": stamp},
            )
        )


@dataclass(frozen=True)
class SlaView:
    reminder_before_hours: int
    escalate_after_hours: int | None
    escalate_to_user_id: uuid.UUID | None
    escalate_to_name: str | None


@dataclass(frozen=True)
class PreferenceView:
    kind: str
    description: str
    email: bool


class NotificationService:
    def __init__(
        self,
        audit: AuditLog,
        policy: PolicyService,
        sender: EmailSender | None = None,
    ) -> None:
        self._audit = audit
        self._policy = policy
        self._sender: EmailSender = sender or NoEmail()

    # Preferences

    def preferences(self, session: Session, principal: Principal) -> list[PreferenceView]:
        chosen = {
            row.kind: row.email
            for row in session.scalars(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == principal.user_id
                )
            )
        }
        return [PreferenceView(k, text, chosen.get(k, False)) for k, text in KINDS.items()]

    def set_preferences(
        self, session: Session, principal: Principal, email: dict[str, bool]
    ) -> list[PreferenceView]:
        unknown = sorted(set(email) - set(KINDS))
        if unknown:
            raise DomainError("These are not kinds of notification: " + ", ".join(unknown) + ".")
        existing = {
            row.kind: row
            for row in session.scalars(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == principal.user_id
                )
            )
        }
        for kind, wanted in email.items():
            row = existing.get(kind)
            if row is None:
                session.add(
                    NotificationPreference(
                        tenant_id=principal.tenant_id,
                        user_id=principal.user_id,
                        kind=kind,
                        email=wanted,
                    )
                )
            else:
                row.email = wanted
        session.flush()
        return self.preferences(session, principal)

    # SLA policy

    def sla(self, session: Session) -> SlaView:
        reminder, escalate, target = sla_for(session)
        user = session.get(User, target) if target else None
        return SlaView(reminder, escalate, target, user.display_name if user else None)

    def set_sla(
        self,
        session: Session,
        principal: Principal,
        reminder_before_hours: int,
        escalate_after_hours: int | None,
        escalate_to_user_id: uuid.UUID | None,
    ) -> SlaView:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        if MANAGE not in held:
            raise Forbidden("You do not have permission to do that.", details={"required": MANAGE})
        if not 0 <= reminder_before_hours <= MAX_HOURS:
            raise DomainError(f"Reminder hours must be 0 to {MAX_HOURS}.")
        if escalate_after_hours is not None and not 0 <= escalate_after_hours <= MAX_HOURS:
            raise DomainError(f"Escalation hours must be 0 to {MAX_HOURS}.")
        if escalate_to_user_id is not None:
            user = session.get(User, escalate_to_user_id)
            if user is None or user.status != "active" or user.kind != "person":
                raise DomainError("Choose an active person to escalate to.")
        row = session.scalar(select(SlaPolicy))
        before = self.sla(session)
        if row is None:
            row = SlaPolicy(tenant_id=principal.tenant_id)
            session.add(row)
        row.reminder_before_hours = reminder_before_hours
        row.escalate_after_hours = escalate_after_hours
        row.escalate_to_user_id = escalate_to_user_id
        row.updated_by = principal.user_id
        session.flush()
        self._audit.record(
            session,
            principal.tenant_id,
            "sla_policy.updated",
            actor=AuditActor(principal.user_id, "user"),
            object_type="sla_policy",
            object_id=str(row.id),
            details={
                "before": [
                    before.reminder_before_hours,
                    before.escalate_after_hours,
                    str(before.escalate_to_user_id) if before.escalate_to_user_id else None,
                ],
                "after": [
                    reminder_before_hours,
                    escalate_after_hours,
                    str(escalate_to_user_id) if escalate_to_user_id else None,
                ],
            },
        )
        return self.sla(session)

    def deliveries(
        self, session: Session, principal: Principal, limit: int = 50
    ) -> list[EmailMessage]:
        return list(
            session.scalars(
                select(EmailMessage)
                .where(EmailMessage.user_id == principal.user_id)
                .order_by(EmailMessage.created_at.desc(), EmailMessage.id)
                .limit(min(limit, 100))
            )
        )

    # Jobs (called by the worker)

    def run_sla(self, db: Database, ctx: JobContext) -> None:
        task_id = uuid.UUID(ctx.payload["task_id"])
        stage = ctx.payload["stage"]
        with db.tenant_session(ctx.tenant_id) as session:
            task = session.get(Task, task_id)
            if task is None or task.status != "open" or task.due_at is None:
                return
            if task.due_at.isoformat() != ctx.payload["due_at"]:
                return  # the due time has changed since, and new timers were made for it
            document = session.get(Document, task.document_id)
            if document is None or document.status != "active":
                return
            tree = self._policy.tree(session, ctx.tenant_id, task.assignee_id)
            if not tree.decide(document.acl_anchor_id, "read").allowed:
                return
            if stage in ("remind", "overdue"):
                kind = "task_reminder" if stage == "remind" else "task_overdue"
                lead = "Due soon" if stage == "remind" else "Overdue"
                add_notification(
                    session,
                    tenant_id=ctx.tenant_id,
                    user_id=task.assignee_id,
                    kind=kind,
                    title=f"{lead}: {task.title}",
                    document_id=document.id,
                    ref_id=task.id,
                )
                return
            _hours, _after, target = sla_for(session)
            target = target or task.created_by
            if target is None or target == task.assignee_id:
                return
            to_tree = self._policy.tree(session, ctx.tenant_id, target)
            if not to_tree.decide(document.acl_anchor_id, "read").allowed:
                return
            who = session.get(User, task.assignee_id)
            add_notification(
                session,
                tenant_id=ctx.tenant_id,
                user_id=target,
                kind="task_escalated",
                title=f"Escalated: {task.title} (for {who.display_name if who else 'someone'})",
                document_id=document.id,
                ref_id=task.id,
            )
            self._audit.record(
                session,
                ctx.tenant_id,
                "task.escalated",
                actor=AuditActor(),
                object_type="task",
                object_id=str(task.id),
                details={"to": str(target), "assignee": str(task.assignee_id)},
            )

    def run_email(self, db: Database, ctx: JobContext) -> None:
        email_id = uuid.UUID(ctx.payload["email_id"])
        with db.tenant_session(ctx.tenant_id) as session:
            mail = session.get(EmailMessage, email_id)
            if mail is None or mail.status != "queued":
                return
            to_address, subject, body = mail.to_address, mail.subject, mail.body
        error: str | None = None
        permanent = False
        try:
            self._sender.send(to_address, subject, body)
        except EmailNotConfigured as exc:
            error, permanent = str(exc), True
        except Exception as exc:  # the mail server is the part we do not control
            error = f"{type(exc).__name__}: {exc}"[:500]
        with db.tenant_session(ctx.tenant_id) as session:
            mail = session.get(EmailMessage, email_id)
            assert mail is not None
            mail.attempts += 1
            if error is None:
                mail.status, mail.sent_at, mail.last_error = "sent", utcnow(), None
                return
            mail.last_error = error
            if permanent or mail.attempts >= MAX_EMAIL_ATTEMPTS:
                mail.status = "failed"
                return
        raise RuntimeError(error)  # the job runner will try again later
