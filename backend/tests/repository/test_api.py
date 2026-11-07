"""F02 over HTTP: the Finance and HR scenario, permission responses and error mapping."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from docuflow.modules.repository.service import RepositoryService
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from tests.identity.support import (
    assign_role,
    audit_events,
    join_group,
    new_group,
    new_role,
    new_user,
)
from tests.repository.support import (
    allow,
    boss_of,
    in_folder,
    make_document,
)
from tests.web_support import API, make_app, sign_in

pytestmark = pytest.mark.db


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    return make_app(db, audit, keys)


def create(client: TestClient, csrf: dict[str, str], path: str, name: str) -> dict:
    response = client.post(f"{API}{path}", json={"name": name}, headers=csrf)
    assert response.status_code == 201, response.text
    return response.json()


def test_finance_and_hr_structure_over_http(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    boss_of(db, tenant)
    boss, csrf = sign_in(app, db, tenant, "boss")
    finance = create(boss, csrf, "/cabinets", "Finance")
    hr = create(boss, csrf, "/cabinets", "HR")
    invoices = create(boss, csrf, f"/cabinets/{finance['id']}/folders", "Supplier Invoices")
    year = create(boss, csrf, f"/folders/{invoices['id']}/folders", "2026")
    november = create(boss, csrf, f"/folders/{year['id']}/folders", "November")
    create(boss, csrf, f"/cabinets/{hr['id']}/folders", "Employee Files")

    detail = boss.get(f"{API}/folders/{november['id']}").json()
    assert [c["name"] for c in detail["breadcrumb"]] == [
        "Finance",
        "Supplier Invoices",
        "2026",
        "November",
    ]
    assert detail["folder"]["depth"] == 3

    root = boss.get(f"{API}/cabinets/{finance['id']}/children").json()
    assert [f["name"] for f in root["folders"]] == ["Supplier Invoices"]
    cabinets = boss.get(f"{API}/cabinets").json()
    assert [c["name"] for c in cabinets["items"]] == ["Finance", "HR"]
    overview = boss.get(f"{API}/cabinets/{finance['id']}").json()
    assert overview["stats"]["subfolders"] == 1
    assert {"read", "create", "manage_acl", "move", "delete"} <= set(overview["permissions"])
    assert "dispose" not in overview["permissions"]


def test_each_team_sees_only_its_own_cabinet(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    boss_of(db, tenant)
    boss, csrf = sign_in(app, db, tenant, "boss")
    finance = create(boss, csrf, "/cabinets", "Finance")
    hr = create(boss, csrf, "/cabinets", "HR")
    for team_name, user_name, cabinet in (
        ("Finance team", "felix", finance),
        ("HR team", "hana", hr),
    ):
        team = new_group(db, tenant, team_name)
        user = new_user(db, tenant, user_name)
        join_group(db, tenant, team, user)
        from tests.identity.support import grant

        grant(db, tenant, uuid.UUID(cabinet["acl_anchor_id"]), "group", team, "read")

    felix, _ = sign_in(app, db, tenant, "felix")
    hana, _ = sign_in(app, db, tenant, "hana")
    assert [c["name"] for c in felix.get(f"{API}/cabinets").json()["items"]] == ["Finance"]
    assert [c["name"] for c in hana.get(f"{API}/cabinets").json()["items"]] == ["HR"]
    assert felix.get(f"{API}/cabinets/{hr['id']}").status_code == 404
    assert hana.get(f"{API}/cabinets/{finance['id']}/children").status_code == 404


def test_refusals_use_the_right_status_and_are_audited(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    boss_of(db, tenant)
    boss, csrf = sign_in(app, db, tenant, "boss")
    finance = create(boss, csrf, "/cabinets", "Finance")
    reader = new_user(db, tenant, "reader")
    allow(db, tenant, uuid.UUID(finance["acl_anchor_id"]), reader, "read")
    client, reader_csrf = sign_in(app, db, tenant, "reader")

    detail = client.get(f"{API}/cabinets/{finance['id']}")
    assert detail.status_code == 200
    assert detail.json()["permissions"] == ["read"]  # the interface can hide what will not work
    denied = client.post(
        f"{API}/cabinets/{finance['id']}/folders", json={"name": "Nope"}, headers=reader_csrf
    )
    assert denied.status_code == 403
    event = audit_events(db, tenant, "access.denied")[-1]
    assert event.actor_id == reader
    assert event.details["path"].endswith("/folders")
    no_cabinet_rights = client.post(f"{API}/cabinets", json={"name": "Mine"}, headers=reader_csrf)
    assert no_cabinet_rights.status_code == 403
    assert client.post(f"{API}/cabinets", json={"name": "Mine"}).status_code == 403  # no CSRF token


def test_conflicts_and_bad_input_map_to_clean_errors(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    boss_of(db, tenant)
    boss, csrf = sign_in(app, db, tenant, "boss")
    finance = create(boss, csrf, "/cabinets", "Finance")
    year = create(boss, csrf, f"/cabinets/{finance['id']}/folders", "2026")
    other = create(boss, csrf, f"/cabinets/{finance['id']}/folders", "Other")

    duplicate = boss.post(
        f"{API}/cabinets/{finance['id']}/folders", json={"name": "2026"}, headers=csrf
    )
    assert (duplicate.status_code, duplicate.json()["error"]["code"]) == (409, "conflict")
    assert boss.post(f"{API}/cabinets", json={"name": "finance"}, headers=csrf).status_code == 409
    bad = boss.post(f"{API}/cabinets/{finance['id']}/folders", json={"name": "a/b"}, headers=csrf)
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "invalid_request")

    rename = boss.patch(
        f"{API}/folders/{year['id']}", json={"row_version": 1, "name": "2027"}, headers=csrf
    )
    assert rename.status_code == 200
    assert rename.json()["row_version"] == 2
    stale = boss.patch(
        f"{API}/folders/{year['id']}", json={"row_version": 1, "name": "2028"}, headers=csrf
    )
    assert stale.status_code == 409

    loop = boss.post(
        f"{API}/folders/{year['id']}/move",
        json={"destination": {"folder_id": year["id"]}, "row_version": 2},
        headers=csrf,
    )
    assert loop.status_code == 409
    both = boss.post(
        f"{API}/folders/{other['id']}/move",
        json={
            "destination": {"folder_id": year["id"], "cabinet_id": finance["id"]},
            "row_version": 1,
        },
        headers=csrf,
    )
    assert both.status_code == 422
    assert boss.get(f"{API}/folders/{uuid.uuid4()}").status_code == 404


def test_moving_copying_archiving_and_deleting_over_http(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    boss_of(db, tenant)
    boss, csrf = sign_in(app, db, tenant, "boss")
    finance = create(boss, csrf, "/cabinets", "Finance")
    hr = create(boss, csrf, "/cabinets", "HR")
    payroll = create(boss, csrf, f"/cabinets/{hr['id']}/folders", "Payroll")
    create(boss, csrf, f"/folders/{payroll['id']}/folders", "2026")

    moved = boss.post(
        f"{API}/folders/{payroll['id']}/move",
        json={"destination": {"cabinet_id": finance["id"]}, "row_version": 1},
        headers=csrf,
    )
    assert moved.status_code == 200
    assert moved.json()["cabinet_id"] == finance["id"]

    copied = boss.post(
        f"{API}/folders/{payroll['id']}/copy",
        json={"destination": {"cabinet_id": hr["id"]}},
        headers=csrf,
    )
    assert copied.status_code == 201
    assert (copied.json()["copied"], copied.json()["skipped"]) == (2, 0)

    assert boss.delete(f"{API}/folders/{payroll['id']}", headers=csrf).status_code == 409
    archived = boss.patch(
        f"{API}/folders/{payroll['id']}",
        json={"row_version": 2, "status": "archived"},
        headers=csrf,
    )
    assert archived.status_code == 200
    assert boss.get(f"{API}/cabinets/{finance['id']}/children").json()["folders"] == []
    shown = boss.get(f"{API}/cabinets/{finance['id']}/children?include_archived=true").json()
    assert [f["name"] for f in shown["folders"]] == ["Payroll"]

    empty = create(boss, csrf, f"/cabinets/{finance['id']}/folders", "Scratch")
    assert boss.delete(f"{API}/folders/{empty['id']}", headers=csrf).status_code == 204
    assert boss.get(f"{API}/folders/{empty['id']}").status_code == 404


def test_documents_over_http(
    app: FastAPI, db: Database, repo: RepositoryService, tenant: uuid.UUID
) -> None:
    boss_principal = boss_of(db, tenant)
    boss, csrf = sign_in(app, db, tenant, "boss")
    finance = create(boss, csrf, "/cabinets", "Finance")
    hr = create(boss, csrf, "/cabinets", "HR")
    inbox = create(boss, csrf, f"/cabinets/{finance['id']}/folders", "Inbox")
    document = make_document(
        db, repo, boss_principal, in_folder(uuid.UUID(inbox["id"])), "invoice.pdf", 2048
    )

    fetched = boss.get(f"{API}/documents/{document}").json()
    assert (fetched["title"], fetched["size_bytes"]) == ("invoice.pdf", 2048)
    listing = boss.get(f"{API}/folders/{inbox['id']}/children").json()
    assert listing["documents"]["total"] == 1
    assert boss.get(f"{API}/folders/{inbox['id']}").json()["stats"]["storage_bytes"] == 2048

    moved = boss.post(
        f"{API}/documents/{document}/move",
        json={"destination": {"cabinet_id": hr["id"]}, "row_version": 1},
        headers=csrf,
    )
    assert moved.status_code == 200
    assert moved.json()["id"] == str(document)
    assert moved.json()["folder_id"] is None

    anchor = boss.post(f"{API}/documents/{document}/acl-anchor", headers=csrf).json()["anchor_id"]
    acl = boss.get(f"{API}/acl/anchors/{anchor}")
    assert acl.status_code == 200
    assert acl.json()["entries"] == []
    assert boss.get(f"{API}/documents/{uuid.uuid4()}").status_code == 404


def test_roles_decide_who_can_manage_cabinets(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    boss_of(db, tenant)
    boss, csrf = sign_in(app, db, tenant, "boss")
    finance = create(boss, csrf, "/cabinets", "Finance")
    helper = new_user(db, tenant, "helper")
    allow(db, tenant, uuid.UUID(finance["acl_anchor_id"]), helper, "read")
    client, helper_csrf = sign_in(app, db, tenant, "helper")
    assert (
        client.post(f"{API}/cabinets", json={"name": "X"}, headers=helper_csrf).status_code == 403
    )
    assign_role(
        db, tenant, new_role(db, tenant, "Cabinet maker", ("cabinets.manage",)), user_id=helper
    )
    assert (
        client.post(f"{API}/cabinets", json={"name": "X"}, headers=helper_csrf).status_code == 201
    )
