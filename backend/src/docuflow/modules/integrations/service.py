"""Telling other systems what happens here, safely and reliably (F20).

Rules that hold throughout:
- Managing webhooks needs the ``integrations.manage`` right.
- A webhook is made from the audit trail. Every event that is recorded is offered to the
  subscriptions of the same organization that asked for it, in the transaction that recorded it.
  There is no second path for events, so a webhook can never report something the audit trail
  does not hold, and a failure to queue a webhook never undoes the action it reports.
- A payload names what happened and what it was about, and carries no content.
- The signing secret is shown once, when it is made or replaced, and is kept encrypted.
- Delivery is a background job that is tried again with growing waits. After the last attempt
  the delivery is marked failed with the reason, and a person can send it again.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.integrations import webhooks
from docuflow.modules.integrations.models import WebhookDelivery, WebhookSubscription
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.crypto import KeyProvider
from docuflow.platform.db import Database, utcnow
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound
from docuflow.platform.jobs import JobContext, enqueue
from docuflow.platform.models import AuditEvent

log = structlog.get_logger("docuflow.webhooks")

MANAGE = "integrations.manage"
JOB_TYPE = "integrations.deliver_webhook"
MAX_ATTEMPTS = 5
MAX_SUBSCRIPTIONS = 50
MAX_NAME = 120
ERROR_LIMIT = 300


@dataclass(frozen=True)
class Created:
    subscription: WebhookSubscription
    # Shown once. It cannot be read again, only replaced.
    secret: str


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


class WebhookService:
    def __init__(
        self,
        audit: AuditLog,
        policy: PolicyService,
        keys: KeyProvider,
        sender: webhooks.Sender | None = None,
        resolver: webhooks.Resolver = webhooks.resolve,
        allow_insecure: bool = False,
    ) -> None:
        self._audit = audit
        self._policy = policy
        self._keys = keys
        self._sender = sender or webhooks.HttpSender()
        self._resolver = resolver
        self._allow_insecure = allow_insecure

    # Helpers

    def _require(self, session: Session, principal: Principal) -> None:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        if MANAGE not in held:
            raise Forbidden("You do not have permission to do that.", details={"required": MANAGE})

    def _record(
        self, session: Session, principal: Principal, action: str, sub_id: uuid.UUID, **details: Any
    ) -> None:
        self._audit.record(
            session,
            principal.tenant_id,
            action,
            actor=AuditActor(
                principal.user_id, "user" if principal.kind == "session" else "api_key"
            ),
            object_type="webhook",
            object_id=str(sub_id),
            details=details,
        )

    def _check_url(self, url: str) -> None:
        try:
            webhooks.check_url(url, allow_insecure=self._allow_insecure, resolver=self._resolver)
        except webhooks.UnsafeUrl as exc:
            raise DomainError(str(exc)) from exc

    @staticmethod
    def _check_events(events: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(events))
        if not cleaned:
            raise DomainError("Choose at least one event.")
        unknown = sorted(set(cleaned) - set(webhooks.EVENTS))
        if unknown:
            raise DomainError("These cannot be subscribed to: " + ", ".join(unknown) + ".")
        return cleaned

    def _get(self, session: Session, sub_id: uuid.UUID) -> WebhookSubscription:
        row = session.get(WebhookSubscription, sub_id)
        if row is None:
            raise NotFound("Webhook not found.")
        return row

    # Subscriptions

    def subscriptions(self, session: Session, principal: Principal) -> list[WebhookSubscription]:
        self._require(session, principal)
        return list(session.scalars(select(WebhookSubscription).order_by(WebhookSubscription.name)))

    def get(self, session: Session, principal: Principal, sub_id: uuid.UUID) -> WebhookSubscription:
        self._require(session, principal)
        return self._get(session, sub_id)

    def create(
        self, session: Session, principal: Principal, name: str, url: str, events: list[str]
    ) -> Created:
        self._require(session, principal)
        name = name.strip()
        if not name or len(name) > MAX_NAME:
            raise DomainError(f"Names must be between 1 and {MAX_NAME} characters.")
        self._check_url(url)
        cleaned = self._check_events(events)
        existing = len(session.scalars(select(WebhookSubscription.id)).all())
        if existing >= MAX_SUBSCRIPTIONS:
            raise DomainError(f"An organization can have at most {MAX_SUBSCRIPTIONS} webhooks.")
        secret = new_secret()
        row = WebhookSubscription(
            tenant_id=principal.tenant_id,
            name=name,
            url=url,
            events=cleaned,
            secret_wrapped=self._keys.wrap(principal.tenant_id, secret.encode()),
            created_by=principal.user_id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise Conflict("A webhook with that name already exists.") from exc
        self._record(
            session, principal, "webhook.created", row.id, name=name, url=url, events=cleaned
        )
        return Created(row, secret)

    def update(
        self,
        session: Session,
        principal: Principal,
        sub_id: uuid.UUID,
        *,
        name: str | None = None,
        url: str | None = None,
        events: list[str] | None = None,
        status: str | None = None,
    ) -> WebhookSubscription:
        self._require(session, principal)
        row = self._get(session, sub_id)
        changes: dict[str, Any] = {}
        if name is not None and name.strip() != row.name:
            clean = name.strip()
            if not clean or len(clean) > MAX_NAME:
                raise DomainError(f"Names must be between 1 and {MAX_NAME} characters.")
            changes["name"] = [row.name, clean]
            row.name = clean
        if url is not None and url != row.url:
            self._check_url(url)
            changes["url"] = [row.url, url]
            row.url = url
        if events is not None:
            cleaned = self._check_events(events)
            if cleaned != row.events:
                changes["events"] = [list(row.events), cleaned]
                row.events = cleaned
        if status is not None and status != row.status:
            if status not in ("active", "disabled"):
                raise DomainError("The status must be active or disabled.")
            changes["status"] = [row.status, status]
            row.status = status
        if changes:
            try:
                session.flush()
            except IntegrityError as exc:
                raise Conflict("A webhook with that name already exists.") from exc
            self._record(session, principal, "webhook.updated", row.id, changes=changes)
        return row

    def delete(self, session: Session, principal: Principal, sub_id: uuid.UUID) -> None:
        self._require(session, principal)
        row = self._get(session, sub_id)
        name = row.name
        session.delete(row)
        self._record(session, principal, "webhook.deleted", sub_id, name=name)

    def rotate_secret(self, session: Session, principal: Principal, sub_id: uuid.UUID) -> Created:
        """Replaces the secret. The old one stops working at once."""
        self._require(session, principal)
        row = self._get(session, sub_id)
        secret = new_secret()
        row.secret_wrapped = self._keys.wrap(principal.tenant_id, secret.encode())
        session.flush()
        self._record(session, principal, "webhook.secret_rotated", row.id, name=row.name)
        return Created(row, secret)

    # Events

    def on_event(self, session: Session, event: AuditEvent) -> None:
        """Offers a recorded event to the subscriptions that want it. Called by the audit log."""
        if event.action not in webhooks.EVENTS:
            return
        subscriptions = session.scalars(
            select(WebhookSubscription).where(
                WebhookSubscription.tenant_id == event.tenant_id,
                WebhookSubscription.status == "active",
            )
        )
        payload = {
            "api_version": "v1",
            "id": str(event.id),
            "type": event.action,
            "occurred_at": event.occurred_at.isoformat(),
            "object": {"type": event.object_type, "id": event.object_id},
        }
        for subscription in subscriptions:
            if event.action in subscription.events:
                self._queue(session, subscription, event.action, payload)

    @staticmethod
    def _queue(
        session: Session,
        subscription: WebhookSubscription,
        event_type: str,
        payload: dict[str, Any],
        redelivery_of: uuid.UUID | None = None,
    ) -> WebhookDelivery:
        delivery = WebhookDelivery(
            tenant_id=subscription.tenant_id,
            subscription_id=subscription.id,
            event_type=event_type,
            payload=payload,
            redelivery_of=redelivery_of,
        )
        session.add(delivery)
        session.flush()
        enqueue(
            session,
            subscription.tenant_id,
            JOB_TYPE,
            f"webhook:{delivery.id}",
            queue="integrations",
            payload={"delivery_id": str(delivery.id)},
        )
        return delivery

    def test(self, session: Session, principal: Principal, sub_id: uuid.UUID) -> WebhookDelivery:
        """Sends a ping, so the address and the signature can be checked before real events."""
        self._require(session, principal)
        row = self._get(session, sub_id)
        payload = {
            "api_version": "v1",
            "id": str(uuid.uuid4()),
            "type": webhooks.PING,
            "occurred_at": utcnow().isoformat(),
            "object": {"type": "webhook", "id": str(row.id)},
        }
        return self._queue(session, row, webhooks.PING, payload)

    def deliveries(
        self, session: Session, principal: Principal, sub_id: uuid.UUID, limit: int = 50
    ) -> list[WebhookDelivery]:
        self._require(session, principal)
        self._get(session, sub_id)
        return list(
            session.scalars(
                select(WebhookDelivery)
                .where(WebhookDelivery.subscription_id == sub_id)
                .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id)
                .limit(max(1, min(limit, 100)))
            )
        )

    def redeliver(
        self, session: Session, principal: Principal, sub_id: uuid.UUID, delivery_id: uuid.UUID
    ) -> WebhookDelivery:
        self._require(session, principal)
        row = self._get(session, sub_id)
        old = session.get(WebhookDelivery, delivery_id)
        if old is None or old.subscription_id != row.id:
            raise NotFound("Delivery not found.")
        if row.status != "active":
            raise Conflict("That webhook is disabled.")
        fresh = self._queue(session, row, old.event_type, old.payload, old.id)
        self._record(session, principal, "webhook.redelivered", row.id, delivery=str(old.id))
        return fresh

    # The job (called by the worker)

    def deliver(self, db: Database, ctx: JobContext) -> None:
        delivery_id = uuid.UUID(ctx.payload["delivery_id"])
        with db.tenant_session(ctx.tenant_id) as session:
            delivery = session.get(WebhookDelivery, delivery_id)
            if delivery is None or delivery.status != "pending":
                return
            subscription = session.get(WebhookSubscription, delivery.subscription_id)
            if subscription is None or subscription.status != "active":
                delivery.status = "failed"
                delivery.last_error = "The webhook is disabled or gone."
                return
            secret = self._keys.unwrap(ctx.tenant_id, subscription.secret_wrapped).decode()
            url, event_type, payload = subscription.url, delivery.event_type, dict(delivery.payload)
        error: str | None = None
        status: int | None = None
        permanent = False
        try:
            # Checked again now, because a name can come to point somewhere it did not.
            webhooks.check_url(url, allow_insecure=self._allow_insecure, resolver=self._resolver)
            body = webhooks.encode(payload)
            stamp = int(utcnow().timestamp())
            status = self._sender.send(
                url,
                {
                    "Content-Type": "application/json",
                    "User-Agent": "DocuFlow-Webhooks/1",
                    webhooks.EVENT_HEADER: event_type,
                    webhooks.DELIVERY_HEADER: str(delivery_id),
                    webhooks.SIGNATURE_HEADER: webhooks.sign(secret, stamp, body),
                },
                body,
            )
            if not 200 <= status < 300:
                error = f"The receiver answered {status}."
        except webhooks.UnsafeUrl as exc:
            error, permanent = str(exc), True  # trying again cannot make it safe
        except Exception as exc:  # the receiver is the part we do not control
            error = f"{type(exc).__name__} while sending."
        with db.tenant_session(ctx.tenant_id) as session:
            delivery = session.get(WebhookDelivery, delivery_id)
            assert delivery is not None
            delivery.attempts += 1
            delivery.response_status = status
            if error is None:
                delivery.status, delivery.delivered_at, delivery.last_error = (
                    "succeeded",
                    utcnow(),
                    None,
                )
                return
            delivery.last_error = error[:ERROR_LIMIT]
            if permanent or delivery.attempts >= MAX_ATTEMPTS:
                delivery.status = "failed"
                log.warning("webhook_gave_up", delivery=str(delivery_id), event_type=event_type)
                return
        raise RuntimeError(error)  # the job runner will try again after a wait
