"""F11 over HTTP: routing a document through approval and task steps."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from docuflow.modules.workflow.service import JOB_TYPE
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from docuflow.platform.jobs import HandlerRegistry, JobRunner
from docuflow.platform.models import ProcessingJob
from tests.documents.files import text_pdf
from tests.documents.test_upload import make, put
from tests.identity.support import assign_role, audit_events, new_role, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    return make_app(db, audit, keys, MemoryObjectStore(), max_upload_bytes=1024 * 1024)


@pytest.fixture
def office(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss = boss_of(db, tenant)
    role = new_role(db, tenant, "Flow admin", ("workflows.manage", "document_types.manage"))
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
    app: FastAPI, db: Database, tenant: uuid.UUID, office: dict[str, Any], name: str, *rights: str
) -> dict[str, Any]:
    user_id = new_user(db, tenant, name)
    allow(
        db,
        tenant,
        uuid.UUID(office["folder"]["acl_anchor_id"]),
        user_id,
        *(rights or ("read",)),
    )
    client, csrf = sign_in(app, db, tenant, name)
    return {"id": str(user_id), "client": client, "csrf": csrf}


def steps_for(*people: tuple[str, str]) -> list[dict[str, Any]]:
    return [
        {"name": f"Step {i + 1}", "kind": kind, "assignee_id": who}
        for i, (kind, who) in enumerate(people)
    ]


def create(office: dict[str, Any], name: str, steps: list[dict[str, Any]], **extra: Any):
    return office["client"].post(
        f"{API}/workflows",
        json={"name": name, "steps": steps, **extra},
        headers=office["csrf"],
    )


def start(who: dict[str, Any], document_id: str, workflow_id: str):
    return who["client"].post(
        f"{API}/documents/{document_id}/workflows",
        json={"workflow_id": workflow_id},
        headers=who["csrf"],
    )


def decide(who: dict[str, Any], step_id: str, decision: str, comment: str = ""):
    return who["client"].post(
        f"{API}/workflow-steps/{step_id}/decision",
        json={"decision": decision, "comment": comment},
        headers=who["csrf"],
    )


def current(office: dict[str, Any], document_id: str) -> dict[str, Any]:
    return office["client"].get(f"{API}/documents/{document_id}/workflows").json()[0]


# Definitions


def test_only_someone_who_manages_workflows_can_make_them(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    clerk = person(app, db, tenant, office, "clerk")
    refused = clerk["client"].post(
        f"{API}/workflows",
        json={"name": "Mine", "steps": steps_for(("approval", clerk["id"]))},
        headers=clerk["csrf"],
    )
    assert refused.status_code == 403
    assert clerk["client"].get(f"{API}/workflows").status_code == 200


def test_steps_are_checked_and_every_problem_is_reported(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    bad = create(
        office,
        "Broken",
        [
            {"name": "", "kind": "approval", "assignee_id": office["id"]},
            {"name": "Ghost", "kind": "task", "assignee_id": str(uuid.uuid4())},
        ],
    )
    assert bad.status_code == 422
    fields = {item["field"] for item in bad.json()["error"]["fields"]}
    assert fields == {"step 1", "step 2"}
    assert create(office, "Empty", []).status_code == 400
    assert (
        create(
            office, "Triggered", steps_for(("task", office["id"])), trigger={"event": "x"}
        ).status_code
        == 400
    )


def test_a_new_version_leaves_running_work_alone(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    alice = person(app, db, tenant, office, "alice")
    made = create(office, "Invoice approval", steps_for(("approval", alice["id"]))).json()
    doc = office["document"]["id"]
    assert start(office, doc, made["id"]).status_code == 201

    published = office["client"].post(
        f"{API}/workflows/{made['id']}/versions",
        json={
            "row_version": made["row_version"],
            "steps": steps_for(("approval", alice["id"]), ("task", office["id"])),
        },
        headers=office["csrf"],
    )
    assert published.status_code == 201, published.text
    assert published.json()["version"] == 2
    running = current(office, doc)
    assert (running["version"], len(running["steps"])) == (1, 1)  # keeps the version it began on
    same = office["client"].post(
        f"{API}/workflows/{made['id']}/versions",
        json={
            "row_version": published.json()["row_version"],
            "steps": steps_for(("approval", alice["id"]), ("task", office["id"])),
        },
        headers=office["csrf"],
    )
    assert same.status_code == 409


# Running


def test_a_document_moves_through_its_steps_to_approval(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    alice = person(app, db, tenant, office, "alice")
    bob = person(app, db, tenant, office, "bob")
    made = create(
        office, "Invoice approval", steps_for(("approval", alice["id"]), ("task", bob["id"]))
    ).json()
    doc = office["document"]["id"]
    started = start(office, doc, made["id"])
    assert started.status_code == 201, started.text
    steps = started.json()["steps"]
    assert [s["status"] for s in steps] == ["active", "pending"]

    # Alice has a task and a notification, and Bob has neither yet.
    assert [t["title"] for t in alice["client"].get(f"{API}/tasks").json()] == ["Approve: Step 1"]
    assert bob["client"].get(f"{API}/tasks").json() == []

    # The task cannot be closed by editing it, and the wrong person cannot decide.
    task = alice["client"].get(f"{API}/tasks").json()[0]
    edited = alice["client"].patch(
        f"{API}/tasks/{task['id']}",
        json={"row_version": task["row_version"], "status": "done"},
        headers=alice["csrf"],
    )
    assert edited.status_code == 409
    assert decide(bob, steps[0]["id"], "approve").status_code == 404

    after = decide(alice, steps[0]["id"], "approve", "Looks right")
    assert after.status_code == 200, after.text
    assert [s["status"] for s in after.json()["steps"]] == ["approved", "active"]
    assert alice["client"].get(f"{API}/tasks").json() == []
    assert len(bob["client"].get(f"{API}/tasks").json()) == 1

    # A task step is finished with "complete", and an approval choice is refused for it.
    assert decide(bob, steps[1]["id"], "approve").status_code == 400
    done = decide(bob, steps[1]["id"], "complete")
    assert done.json()["status"] == "completed"
    notes = office["client"].get(f"{API}/notifications").json()["items"]
    assert any(item["kind"] == "workflow_finished" for item in notes)
    kinds = [e["kind"] for e in done.json()["events"]]
    assert kinds == [
        "started",
        "step_activated",
        "decided",
        "step_activated",
        "decided",
        "completed",
    ]
    assert len(audit_events(db, tenant, "workflow.step_decided")) == 2
    assert decide(bob, steps[1]["id"], "complete").status_code == 409


def test_a_rejection_needs_a_reason_and_ends_the_workflow(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    alice = person(app, db, tenant, office, "alice")
    bob = person(app, db, tenant, office, "bob")
    made = create(office, "Approval", steps_for(("approval", alice["id"]), ("task", bob["id"])))
    doc = office["document"]["id"]
    steps = start(office, doc, made.json()["id"]).json()["steps"]
    assert decide(alice, steps[0]["id"], "reject").status_code == 400
    rejected = decide(alice, steps[0]["id"], "reject", "Wrong amount")
    assert rejected.status_code == 200
    body = rejected.json()
    assert body["status"] == "rejected"
    assert [s["status"] for s in body["steps"]] == ["rejected", "skipped"]
    assert body["steps"][0]["comment"] == "Wrong amount"
    assert bob["client"].get(f"{API}/tasks").json() == []
    notes = office["client"].get(f"{API}/notifications").json()["items"]
    assert any(item["title"].startswith("Rejected") for item in notes)
    # It can be started again afterwards.
    assert start(office, doc, made.json()["id"]).status_code == 201


def test_starting_is_refused_when_it_cannot_work(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    alice = person(app, db, tenant, office, "alice")
    outsider_id = str(new_user(db, tenant, "outsider"))
    doc = office["document"]["id"]
    blocked = create(office, "Needs outsider", steps_for(("approval", outsider_id)))
    refused = start(office, doc, blocked.json()["id"])
    assert refused.status_code == 409
    assert "outsider" in refused.json()["error"]["message"]

    fine = create(office, "Fine", steps_for(("approval", alice["id"]))).json()
    assert start(office, doc, fine["id"]).status_code == 201
    again = start(office, doc, fine["id"])
    assert again.status_code == 409
    assert "already running" in again.json()["error"]["message"]

    reader = person(app, db, tenant, office, "reader")
    assert start(reader, doc, fine["id"]).status_code == 403


def test_a_workflow_can_be_stopped(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    alice = person(app, db, tenant, office, "alice")
    made = create(office, "Approval", steps_for(("approval", alice["id"]))).json()
    doc = office["document"]["id"]
    instance = start(office, doc, made["id"]).json()
    other = person(app, db, tenant, office, "other")
    assert (
        other["client"]
        .post(f"{API}/workflow-instances/{instance['id']}/cancel", headers=other["csrf"])
        .status_code
        == 403
    )
    stopped = office["client"].post(
        f"{API}/workflow-instances/{instance['id']}/cancel", headers=office["csrf"]
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "cancelled"
    assert alice["client"].get(f"{API}/tasks").json() == []
    assert (
        office["client"]
        .post(f"{API}/workflow-instances/{instance['id']}/cancel", headers=office["csrf"])
        .status_code
        == 409
    )


# Delegation


def test_a_delegation_sends_new_steps_to_someone_else(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    alice = person(app, db, tenant, office, "alice")
    bob = person(app, db, tenant, office, "bob")
    made = create(office, "Approval", steps_for(("approval", alice["id"]))).json()
    now = datetime.now(UTC)
    gone = alice["client"].post(
        f"{API}/delegations",
        json={
            "to_user_id": bob["id"],
            "starts_at": (now - timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(days=3)).isoformat(),
        },
        headers=alice["csrf"],
    )
    assert gone.status_code == 201, gone.text
    assert alice["client"].get(f"{API}/delegations").json()[0]["to_name"] == "bob"

    steps = start(office, office["document"]["id"], made["id"]).json()["steps"]
    assert steps[0]["assignee_name"] == "bob"
    assert steps[0]["delegated_from"] == "alice"
    assert decide(alice, steps[0]["id"], "approve").status_code == 404
    assert decide(bob, steps[0]["id"], "approve").status_code == 200

    removed = alice["client"].delete(
        f"{API}/delegations/{gone.json()['id']}", headers=alice["csrf"]
    )
    assert removed.status_code == 204
    assert alice["client"].get(f"{API}/delegations").json() == []


def test_bad_delegations_are_refused(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    alice = person(app, db, tenant, office, "alice")
    now = datetime.now(UTC)

    def send(to: str, start_at: datetime, end_at: datetime) -> int:
        return (
            alice["client"]
            .post(
                f"{API}/delegations",
                json={
                    "to_user_id": to,
                    "starts_at": start_at.isoformat(),
                    "ends_at": end_at.isoformat(),
                },
                headers=alice["csrf"],
            )
            .status_code
        )

    assert send(alice["id"], now, now + timedelta(days=1)) == 400
    assert send(office["id"], now + timedelta(days=1), now) == 400
    assert send(str(uuid.uuid4()), now, now + timedelta(days=1)) == 400


# Starting by itself


def test_giving_a_document_a_type_starts_the_workflows_for_that_type(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    alice = person(app, db, tenant, office, "alice")
    client, csrf = office["client"], office["csrf"]
    kind = client.post(
        f"{API}/document-types",
        json={"name": "Invoice", "fields": [{"key": "ref", "label": "Ref", "data_type": "text"}]},
        headers=csrf,
    ).json()
    made = create(
        office,
        "Auto approval",
        steps_for(("approval", alice["id"])),
        trigger={"event": "document.typed", "document_type_id": kind["id"]},
    )
    assert made.status_code == 201, made.text
    doc = office["document"]["id"]
    assert client.get(f"{API}/documents/{doc}/workflows").json() == []

    saved = client.put(
        f"{API}/documents/{doc}/index",
        json={"document_type_id": kind["id"], "values": {"ref": "A1"}},
        headers=csrf,
    )
    assert saved.status_code == 200, saved.text

    registry = HandlerRegistry()
    registry.register(JOB_TYPE)(lambda job: app.state.workflow.run(db, job))
    runner = JobRunner(db, registry, app.state.text._audit, 0)
    with db.tenant_session(tenant) as session:
        ids = list(
            session.scalars(
                select(ProcessingJob.id).where(
                    ProcessingJob.type == JOB_TYPE, ProcessingJob.status == "queued"
                )
            )
        )
    assert len(ids) == 1
    runner.run(tenant, ids[0])

    instance = current(office, doc)
    assert instance["workflow_name"] == "Auto approval"
    began = next(e for e in instance["events"] if e["kind"] == "started")
    assert began["details"]["automatic"] is True
    assert len(alice["client"].get(f"{API}/tasks").json()) == 1

    # Saving again without changing the type does not start it twice.
    client.put(
        f"{API}/documents/{doc}/index",
        json={"document_type_id": kind["id"], "values": {"ref": "A2"}},
        headers=csrf,
    )
    with db.tenant_session(tenant) as session:
        queued = session.scalars(
            select(ProcessingJob.id).where(
                ProcessingJob.type == JOB_TYPE, ProcessingJob.status == "queued"
            )
        ).all()
    assert queued == []


def test_workflows_are_hidden_from_people_who_cannot_read_the_document(
    app: FastAPI, office: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    alice = person(app, db, tenant, office, "alice")
    made = create(office, "Approval", steps_for(("approval", alice["id"]))).json()
    doc = office["document"]["id"]
    start(office, doc, made["id"])
    stranger_id = new_user(db, tenant, "stranger")
    stranger, _ = sign_in(app, db, tenant, "stranger")
    assert stranger_id
    assert stranger.get(f"{API}/documents/{doc}/workflows").status_code == 404
