"""F10 over HTTP: comments, mentions, tasks and notifications, within what a person may read."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import delete

from docuflow.modules.identity.models import AclEntry
from docuflow.modules.identity.policy import bump_permission_epoch
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from tests.documents.files import text_pdf
from tests.documents.test_upload import make, put
from tests.identity.support import audit_events, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    return make_app(db, audit, keys, MemoryObjectStore(), max_upload_bytes=1024 * 1024)


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss_of(db, tenant)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Invoices")
    document = put(client, csrf, folder["id"], "invoice.pdf", text_pdf(["Invoice text"])).json()[
        "document"
    ]
    return {"client": client, "csrf": csrf, "folder": folder, "document": document}


def person(
    app: FastAPI, db: Database, tenant: uuid.UUID, office: dict[str, Any], name: str, *rights: str
) -> dict[str, Any]:
    user_id = new_user(db, tenant, name)
    if rights:
        allow(db, tenant, uuid.UUID(office["folder"]["acl_anchor_id"]), user_id, *rights)
    client, csrf = sign_in(app, db, tenant, name)
    return {"id": str(user_id), "client": client, "csrf": csrf}


def comment(who: dict[str, Any], document_id: str, body: str):
    return who["client"].post(
        f"{API}/documents/{document_id}/comments", json={"body": body}, headers=who["csrf"]
    )


def task(who: dict[str, Any], document_id: str, assignee: str, **extra: Any):
    return who["client"].post(
        f"{API}/documents/{document_id}/tasks",
        json={"title": "Check the total", "assignee_id": assignee, **extra},
        headers=who["csrf"],
    )


def unread(who: dict[str, Any]) -> dict[str, Any]:
    return who["client"].get(f"{API}/notifications").json()


# Comments


def test_a_comment_mentions_people_who_can_read_and_notifies_only_them(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    clerk = person(app, db, tenant, office, "clerk", "read")
    outsider = person(app, db, tenant, office, "outsider")
    made = comment(office, doc, "Please review, @clerk and @outsider. Thanks @nobody")
    assert made.status_code == 201, made.text
    assert made.json()["mentioned"] == ["clerk"]  # the outsider cannot read it, so is not told

    assert unread(clerk)["unread"] == 1
    assert unread(clerk)["items"][0]["kind"] == "mention"
    assert unread(outsider)["unread"] == 0
    (event,) = audit_events(db, tenant, "document.commented")
    assert event.details["mentions"] == ["clerk"]

    listed = clerk["client"].get(f"{API}/documents/{doc}/comments").json()
    assert [(item["author_name"], item["body"][:6]) for item in listed] == [("boss", "Please")]


def test_commenting_needs_the_annotate_right_and_reading(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    reader = person(app, db, tenant, office, "reader", "read")
    assert comment(reader, doc, "Hello").status_code == 403
    allow(
        db,
        tenant,
        uuid.UUID(office["folder"]["acl_anchor_id"]),
        uuid.UUID(reader["id"]),
        "annotate",
    )
    assert comment(reader, doc, "Hello").status_code == 201
    stranger = person(app, db, tenant, office, "stranger")
    assert comment(stranger, doc, "Hi").status_code == 404
    assert stranger["client"].get(f"{API}/documents/{doc}/comments").status_code == 404
    assert comment(office, doc, "   ").status_code in (400, 422)
    assert comment(office, doc, "x" * 4001).status_code == 422


def test_a_comment_is_removed_by_its_author_or_someone_who_can_delete(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    writer = person(app, db, tenant, office, "writer", "read", "annotate")
    other = person(app, db, tenant, office, "other", "read", "annotate")
    mine = comment(writer, doc, "My note").json()["id"]
    url = f"{API}/documents/{doc}/comments/{mine}"
    assert other["client"].delete(url, headers=other["csrf"]).status_code == 403
    assert writer["client"].delete(url, headers=writer["csrf"]).status_code == 204
    theirs = comment(writer, doc, "Another").json()["id"]
    removed = office["client"].delete(
        f"{API}/documents/{doc}/comments/{theirs}", headers=office["csrf"]
    )
    assert removed.status_code == 204
    assert office["client"].get(f"{API}/documents/{doc}/comments").json() == []
    assert len(audit_events(db, tenant, "document.comment_removed")) == 2


# Tasks


def test_a_task_is_given_to_someone_who_can_read_and_they_are_told(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    clerk = person(app, db, tenant, office, "clerk", "read")
    outsider = person(app, db, tenant, office, "outsider")
    options = office["client"].get(f"{API}/documents/{doc}/assignable").json()
    assert {item["name"] for item in options} == {"boss", "clerk"}

    refused = task(office, doc, outsider["id"])
    assert refused.status_code == 400
    assert "cannot read this document" in refused.json()["error"]["message"]

    made = task(office, doc, clerk["id"], priority="high", due_at="2026-12-01T09:00:00Z")
    assert made.status_code == 201, made.text
    assert (made.json()["status"], made.json()["priority"]) == ("open", "high")
    notes = unread(clerk)
    assert notes["unread"] == 1
    assert notes["items"][0]["kind"] == "task_assigned"

    mine = clerk["client"].get(f"{API}/tasks").json()
    assert [item["title"] for item in mine] == ["Check the total"]
    assert office["client"].get(f"{API}/tasks", params={"mine": True}).json() == []
    assert len(audit_events(db, tenant, "task.created")) == 1


def test_the_assignee_completes_a_task_and_the_maker_is_told(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    clerk = person(app, db, tenant, office, "clerk", "read")
    bystander = person(app, db, tenant, office, "bystander", "read")
    made = task(office, doc, clerk["id"]).json()
    url = f"{API}/tasks/{made['id']}"

    denied = bystander["client"].patch(
        url, json={"row_version": 1, "status": "done"}, headers=bystander["csrf"]
    )
    assert denied.status_code == 403
    done = clerk["client"].patch(
        url, json={"row_version": 1, "status": "done"}, headers=clerk["csrf"]
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done"
    assert done.json()["completed_at"]
    assert any(item["kind"] == "task_done" for item in unread(office)["items"])
    stale = clerk["client"].patch(
        url, json={"row_version": 1, "status": "open"}, headers=clerk["csrf"]
    )
    assert stale.status_code == 409
    assert clerk["client"].get(f"{API}/tasks").json() == []  # open tasks only by default
    both = clerk["client"].get(f"{API}/tasks", params={"status": "done"}).json()
    assert len(both) == 1


def test_changing_a_task_needs_the_right_to_edit_the_document(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    clerk = person(app, db, tenant, office, "clerk", "read")
    other = person(app, db, tenant, office, "other", "read")
    made = task(office, doc, clerk["id"]).json()
    url = f"{API}/tasks/{made['id']}"
    refused = clerk["client"].patch(
        url, json={"row_version": 1, "priority": "low"}, headers=clerk["csrf"]
    )
    assert refused.status_code == 403
    moved = office["client"].patch(
        url, json={"row_version": 1, "assignee_id": other["id"]}, headers=office["csrf"]
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["assignee_name"] == "other"
    assert unread(other)["unread"] == 1


def test_a_task_cannot_be_made_by_someone_who_cannot_edit(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    reader = person(app, db, tenant, office, "reader", "read")
    assert task(reader, doc, reader["id"]).status_code == 403


# Notifications


def test_notifications_can_be_read_one_by_one_or_all_at_once(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    clerk = person(app, db, tenant, office, "clerk", "read")
    task(office, doc, clerk["id"])
    comment(office, doc, "Look @clerk")
    notes = unread(clerk)
    assert notes["unread"] == 2
    client, csrf = clerk["client"], clerk["csrf"]
    first = notes["items"][0]["id"]
    assert client.post(f"{API}/notifications/{first}/read", headers=csrf).status_code == 204
    assert unread(clerk)["unread"] == 1
    assert (
        len(client.get(f"{API}/notifications", params={"unread_only": True}).json()["items"]) == 1
    )
    assert client.post(f"{API}/notifications/read-all", headers=csrf).json() == {"marked": 1}
    assert unread(clerk)["unread"] == 0
    # Nobody else's notification can be touched.
    assert (
        office["client"]
        .post(f"{API}/notifications/{first}/read", headers=office["csrf"])
        .status_code
        == 404
    )


def test_a_notification_disappears_when_the_document_is_no_longer_readable(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    doc = office["document"]["id"]
    clerk = person(app, db, tenant, office, "clerk", "read")
    comment(office, doc, "For you @clerk")
    assert len(unread(clerk)["items"]) == 1
    with db.tenant_session(tenant) as session:
        session.execute(delete(AclEntry).where(AclEntry.principal_id == uuid.UUID(clerk["id"])))
        bump_permission_epoch(session, tenant)
    assert unread(clerk)["items"] == []
