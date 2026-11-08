"""F20 over HTTP: subscribing to events, and what is sent when they happen."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from docuflow.modules.integrations import webhooks
from docuflow.modules.integrations.models import WebhookDelivery, WebhookSubscription
from docuflow.modules.integrations.service import JOB_TYPE, MAX_ATTEMPTS
from docuflow.platform.audit import AuditEvent, AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from docuflow.platform.jobs import JobContext
from docuflow.platform.models import ProcessingJob
from tests.documents.files import text_pdf
from tests.documents.test_upload import make, put
from tests.identity.support import assign_role, audit_events, new_role, new_user
from tests.repository.support import boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db

PUBLIC = "93.184.216.34"


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes]] = []
        self.status = 200
        self.error: Exception | None = None

    def send(self, url: str, headers: dict[str, str], body: bytes) -> int:
        self.calls.append((url, headers, body))
        if self.error is not None:
            raise self.error
        return self.status


class Resolver:
    def __init__(self) -> None:
        self.table: dict[str, list[str]] = {}

    def __call__(self, host: str, port: int) -> list[str]:
        return self.table.get(host, [PUBLIC])


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def resolver() -> Resolver:
    return Resolver()


@pytest.fixture
def app(
    db: Database,
    audit: AuditLog,
    keys: LocalKeyProvider,
    sender: FakeSender,
    resolver: Resolver,
) -> FastAPI:
    app = make_app(db, audit, keys, MemoryObjectStore(), max_upload_bytes=1024 * 1024)
    app.state.webhooks._sender = sender
    app.state.webhooks._resolver = resolver
    return app


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss = boss_of(db, tenant)
    role = new_role(db, tenant, "Integrator", ("integrations.manage",))
    assign_role(db, tenant, role, user_id=boss.user_id)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Invoices")
    document = put(
        client, csrf, folder["id"], "secret-merger.pdf", text_pdf(["Private text"])
    ).json()["document"]
    return {"client": client, "csrf": csrf, "folder": folder, "document": document}


def subscribe(
    office: dict[str, Any],
    name: str = "Receiver",
    url: str = "https://hooks.example.com/in",
    events: list[str] | None = None,
):
    return office["client"].post(
        f"{API}/webhooks",
        json={
            "name": name,
            "url": url,
            "events": ["document.commented"] if events is None else events,
        },
        headers=office["csrf"],
    )


def comment(office: dict[str, Any], body: str = "Looks fine") -> None:
    response = office["client"].post(
        f"{API}/documents/{office['document']['id']}/comments",
        json={"body": body},
        headers=office["csrf"],
    )
    assert response.status_code == 201, response.text


def deliveries(db: Database, tenant: uuid.UUID) -> list[WebhookDelivery]:
    with db.tenant_session(tenant) as session:
        return list(session.scalars(select(WebhookDelivery).order_by(WebhookDelivery.created_at)))


def run(app: FastAPI, db: Database, tenant: uuid.UUID, delivery: WebhookDelivery) -> None:
    ctx = JobContext(tenant, uuid.uuid4(), {"delivery_id": str(delivery.id)}, lambda _p: None)
    app.state.webhooks.deliver(db, ctx)


# Managing them


def test_only_someone_who_manages_integrations_can_see_or_change_webhooks(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    hook = subscribe(office).json()
    new_user(db, tenant, "clerk")
    clerk, csrf = sign_in(app, db, tenant, "clerk")
    assert clerk.get(f"{API}/webhooks").status_code == 403
    assert clerk.get(f"{API}/webhooks/{hook['id']}").status_code == 403
    body = {"name": "X", "url": "https://hooks.example.com/x", "events": ["task.created"]}
    assert clerk.post(f"{API}/webhooks", json=body, headers=csrf).status_code == 403
    assert clerk.delete(f"{API}/webhooks/{hook['id']}", headers=csrf).status_code == 403
    assert clerk.get(f"{API}/webhook-events").status_code == 200  # the list of events is not secret


def test_the_secret_is_shown_once_and_kept_only_encrypted(
    app: FastAPI,
    office: dict[str, Any],
    db: Database,
    tenant: uuid.UUID,
    keys: LocalKeyProvider,
) -> None:
    made = subscribe(office)
    assert made.status_code == 201, made.text
    secret = made.json()["secret"]
    assert secret.startswith("whsec_")
    listed = office["client"].get(f"{API}/webhooks").json()
    assert "secret" not in listed[0]
    assert secret not in office["client"].get(f"{API}/webhooks/{made.json()['id']}").text
    with db.tenant_session(tenant) as session:
        row = session.scalars(select(WebhookSubscription)).one()
        assert secret.encode() not in row.secret_wrapped
        assert keys.unwrap(tenant, row.secret_wrapped).decode() == secret
    created = audit_events(db, tenant, "webhook.created")
    assert len(created) == 1
    assert secret not in json.dumps(created[0].details)


def test_unsafe_or_unusable_subscriptions_are_refused_with_a_reason(
    office: dict[str, Any], resolver: Resolver
) -> None:
    resolver.table["internal.example.com"] = ["10.0.0.7"]
    resolver.table["gone.example.com"] = []
    refused = {
        "http://hooks.example.com/in": "https",
        "https://internal.example.com/in": "private or internal",
        "https://gone.example.com/in": "cannot be found",
        "https://user:pw@hooks.example.com/in": "user name",
    }
    for url, why in refused.items():
        response = subscribe(office, url=url)
        assert response.status_code == 400, url
        assert why in response.json()["error"]["message"]
    bad_event = subscribe(office, events=["document.previewed"])
    assert bad_event.status_code == 400
    assert "cannot be subscribed to" in bad_event.json()["error"]["message"]
    assert subscribe(office, events=[]).status_code == 422
    assert subscribe(office).status_code == 201
    assert subscribe(office).status_code == 409  # the same name


def test_a_webhook_can_be_changed_disabled_and_deleted(
    office: dict[str, Any], resolver: Resolver, db: Database, tenant: uuid.UUID
) -> None:
    client, csrf = office["client"], office["csrf"]
    hook = subscribe(office).json()
    url = f"{API}/webhooks/{hook['id']}"
    changed = client.patch(
        url, json={"name": "Renamed", "events": ["task.created", "task.updated"]}, headers=csrf
    )
    assert changed.status_code == 200
    assert changed.json()["events"] == ["task.created", "task.updated"]
    resolver.table["internal.example.com"] = ["10.0.0.7"]
    unsafe = client.patch(url, json={"url": "https://internal.example.com/x"}, headers=csrf)
    assert unsafe.status_code == 400
    assert client.get(url).json()["url"] == "https://hooks.example.com/in"  # unchanged
    assert (
        client.patch(url, json={"status": "disabled"}, headers=csrf).json()["status"] == "disabled"
    )
    assert client.delete(url, headers=csrf).status_code == 204
    assert client.get(url).status_code == 404
    assert len(audit_events(db, tenant, "webhook.deleted")) == 1


# What is sent


def test_an_event_is_queued_for_each_subscription_that_wants_it_and_nobody_else(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    wants = subscribe(office, "Wants comments", events=["document.commented"]).json()
    subscribe(office, "Wants tasks", url="https://hooks.example.com/t", events=["task.created"])
    off = subscribe(office, "Disabled", url="https://hooks.example.com/d").json()
    office["client"].patch(
        f"{API}/webhooks/{off['id']}", json={"status": "disabled"}, headers=office["csrf"]
    )
    with db.tenant_session(other_tenant) as session:
        session.add(
            WebhookSubscription(
                tenant_id=other_tenant,
                name="Someone else",
                url="https://hooks.example.com/o",
                events=["document.commented"],
                secret_wrapped=b"x",
            )
        )
    comment(office)

    queued = deliveries(db, tenant)
    assert [str(d.subscription_id) for d in queued] == [wants["id"]]
    assert deliveries(db, other_tenant) == []  # another organization's webhook is never called
    with db.tenant_session(tenant) as session:
        jobs = list(session.scalars(select(ProcessingJob).where(ProcessingJob.type == JOB_TYPE)))
    assert [(j.queue, j.payload["delivery_id"]) for j in jobs] == [
        ("integrations", str(queued[0].id))
    ]


def test_a_call_is_signed_and_carries_what_happened_but_no_content(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID, sender: FakeSender
) -> None:
    secret = subscribe(office).json()["secret"]
    comment(office, "The merger price is 40 million")
    (delivery,) = deliveries(db, tenant)
    run(app, db, tenant, delivery)

    (url, headers, body) = sender.calls[0]
    assert url == "https://hooks.example.com/in"
    assert webhooks.verify(secret, headers[webhooks.SIGNATURE_HEADER], body)
    assert headers[webhooks.EVENT_HEADER] == "document.commented"
    assert headers[webhooks.DELIVERY_HEADER] == str(delivery.id)
    sent = json.loads(body)
    assert set(sent) == {"api_version", "id", "type", "occurred_at", "object"}
    assert sent["type"] == "document.commented"
    assert sent["object"] == {"type": "document", "id": office["document"]["id"]}
    assert b"merger" not in body  # neither the comment nor the document's name leaves
    assert b"40 million" not in body
    done = deliveries(db, tenant)[0]
    assert (done.status, done.attempts, done.response_status) == ("succeeded", 1, 200)
    assert done.delivered_at is not None


def test_a_failing_receiver_is_tried_again_and_then_given_up_on(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID, sender: FakeSender
) -> None:
    subscribe(office)
    comment(office)
    (delivery,) = deliveries(db, tenant)
    sender.status = 503
    for attempt in range(1, MAX_ATTEMPTS):
        with pytest.raises(RuntimeError):
            run(app, db, tenant, delivery)
        row = deliveries(db, tenant)[0]
        assert (row.status, row.attempts) == ("pending", attempt)
    run(app, db, tenant, delivery)  # the last attempt ends it quietly
    row = deliveries(db, tenant)[0]
    assert (row.status, row.attempts, row.response_status) == ("failed", MAX_ATTEMPTS, 503)
    assert row.last_error == "The receiver answered 503."
    run(app, db, tenant, delivery)  # a finished delivery is never sent again
    assert len(sender.calls) == MAX_ATTEMPTS


def test_a_network_error_is_recorded_without_its_details(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID, sender: FakeSender
) -> None:
    subscribe(office)
    comment(office)
    (delivery,) = deliveries(db, tenant)
    sender.error = ConnectionError("secret-internal-host:5432 refused")
    with pytest.raises(RuntimeError):
        run(app, db, tenant, delivery)
    row = deliveries(db, tenant)[0]
    assert row.last_error == "ConnectionError while sending."
    assert "5432" not in (row.last_error or "")


def test_an_address_that_has_come_to_point_inside_the_network_is_never_called(
    app: FastAPI,
    office: dict[str, Any],
    db: Database,
    tenant: uuid.UUID,
    sender: FakeSender,
    resolver: Resolver,
) -> None:
    subscribe(office)
    comment(office)
    (delivery,) = deliveries(db, tenant)
    resolver.table["hooks.example.com"] = ["169.254.169.254"]  # the name now points at metadata
    run(app, db, tenant, delivery)  # no retry: waiting cannot make it safe
    row = deliveries(db, tenant)[0]
    assert (row.status, row.attempts) == ("failed", 1)
    assert "private or internal" in (row.last_error or "")
    assert sender.calls == []


def test_a_delivery_for_a_webhook_that_was_disabled_meanwhile_is_not_sent(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID, sender: FakeSender
) -> None:
    hook = subscribe(office).json()
    comment(office)
    (delivery,) = deliveries(db, tenant)
    office["client"].patch(
        f"{API}/webhooks/{hook['id']}", json={"status": "disabled"}, headers=office["csrf"]
    )
    run(app, db, tenant, delivery)
    assert sender.calls == []
    assert deliveries(db, tenant)[0].status == "failed"


def test_a_ping_checks_the_address_and_a_failed_call_can_be_sent_again(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID, sender: FakeSender
) -> None:
    client, csrf = office["client"], office["csrf"]
    hook = subscribe(office).json()
    ping = client.post(f"{API}/webhooks/{hook['id']}/test", headers=csrf)
    assert ping.status_code == 202
    assert ping.json()["event_type"] == webhooks.PING
    (delivery,) = deliveries(db, tenant)
    run(app, db, tenant, delivery)
    assert json.loads(sender.calls[0][2])["type"] == webhooks.PING

    again = client.post(
        f"{API}/webhooks/{hook['id']}/deliveries/{delivery.id}/redeliver", headers=csrf
    )
    assert again.status_code == 202
    assert again.json()["redelivery_of"] == str(delivery.id)
    log = client.get(f"{API}/webhooks/{hook['id']}/deliveries").json()
    assert [d["status"] for d in log] == ["pending", "succeeded"]  # newest first
    missing = client.post(
        f"{API}/webhooks/{hook['id']}/deliveries/{uuid.uuid4()}/redeliver", headers=csrf
    )
    assert missing.status_code == 404


def test_replacing_the_secret_stops_the_old_one_working(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID, sender: FakeSender
) -> None:
    hook = subscribe(office).json()
    old = hook["secret"]
    rotated = office["client"].post(f"{API}/webhooks/{hook['id']}/secret", headers=office["csrf"])
    assert rotated.status_code == 200
    new = rotated.json()["secret"]
    assert new != old
    comment(office)
    (delivery,) = deliveries(db, tenant)
    run(app, db, tenant, delivery)
    _url, headers, body = sender.calls[0]
    assert webhooks.verify(new, headers[webhooks.SIGNATURE_HEADER], body)
    assert not webhooks.verify(old, headers[webhooks.SIGNATURE_HEADER], body)
    assert len(audit_events(db, tenant, "webhook.secret_rotated")) == 1


def test_a_webhook_that_breaks_can_never_undo_the_action_it_reports(
    app: FastAPI,
    office: dict[str, Any],
    db: Database,
    tenant: uuid.UUID,
    audit: AuditLog,
) -> None:
    subscribe(office)

    def boom(session: Any, event: AuditEvent) -> None:
        raise RuntimeError("the listener is broken")

    audit.add_listener(boom, "webhooks")  # replaces the working one
    try:
        comment(office)  # still succeeds
        comments = (
            office["client"].get(f"{API}/documents/{office['document']['id']}/comments").json()
        )
        assert [c["body"] for c in comments] == ["Looks fine"]
        assert deliveries(db, tenant) == []
    finally:
        audit.add_listener(app.state.webhooks.on_event, "webhooks")
    comment(office, "Second")
    assert len(deliveries(db, tenant)) == 1  # and it works again once it is fixed


def test_the_webhooks_own_events_and_reads_are_never_sent(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    subscribe(office, events=["document.commented", "task.created"])
    office["client"].get(f"{API}/documents/{office['document']['id']}/download")
    office["client"].get(f"{API}/documents/{office['document']['id']}/preview/pages/1")
    assert deliveries(db, tenant) == []
