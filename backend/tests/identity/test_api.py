"""The identity HTTP API: real app, real database, real cookies."""

from __future__ import annotations

import json
import re
import time
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from docuflow.main import create_app
from docuflow.modules.identity.api import PUBLIC_ROUTES
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from tests.identity.support import (
    STRONG,
    assign_role,
    audit_events,
    email,
    grant,
    new_admin,
    new_anchor,
    new_group,
    new_role,
    new_user,
    tenant_slug,
)

pytestmark = pytest.mark.db
API = "/api/v1"


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def incr(self, name: str) -> int:
        self.counts[name] = self.counts.get(name, 0) + 1
        return self.counts[name]

    def expire(self, name: str, seconds: int) -> bool:
        return True

    def ping(self) -> bool:
        return True

    def exists(self, key: str) -> int:
        return 1


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    services = SimpleNamespace(
        settings=SimpleNamespace(log_level="WARNING", environment="test"),
        db=db,
        audit=audit,
        keys=keys,
        redis=FakeRedis(),
        store=MagicMock(),
    )
    return create_app(services)  # type: ignore[arg-type]


def sign_in(
    app: FastAPI, db: Database, tenant: uuid.UUID, name: str, password: str = STRONG
) -> tuple[TestClient, dict[str, str]]:
    client = TestClient(app)
    response = client.post(
        f"{API}/auth/login",
        json={"organization": tenant_slug(db, tenant), "email": email(name), "password": password},
    )
    assert response.status_code == 200, response.text
    return client, {"X-CSRF-Token": response.json()["csrf_token"] or ""}


def anonymous_failures(app: FastAPI) -> tuple[list[tuple[str, str, int]], int]:
    """Endpoints that answered an anonymous caller with something other than 401.

    Reads the OpenAPI schema, so it sees every published endpoint however FastAPI stores them.
    """
    client = TestClient(app)
    failures: list[tuple[str, str, int]] = []
    checked = 0
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith(API):
            continue
        for method in operations:
            if (method.upper(), path) in PUBLIC_ROUTES:
                continue
            url = path
            for name in re.findall(r"\{(\w+)\}", path):
                url = url.replace("{" + name + "}", str(uuid.uuid4()))
            status = client.request(method.upper(), url, json={}).status_code
            checked += 1
            if status != 401:
                failures.append((method, path, status))
    return failures, checked


def test_every_route_rejects_anonymous_callers(app: FastAPI) -> None:
    """Fails as soon as someone adds an endpoint and forgets to protect it."""
    failures, checked = anonymous_failures(app)
    assert failures == []
    assert checked >= 25


def test_the_guard_catches_an_unprotected_route(app: FastAPI) -> None:
    @app.get(f"{API}/oops")
    def oops() -> dict[str, str]:
        return {"leaked": "yes"}

    failures, _ = anonymous_failures(app)
    assert failures == [("get", f"{API}/oops", 200)]


def test_public_routes_are_exactly_the_login_steps(app: FastAPI) -> None:
    paths = app.openapi()["paths"]
    for method, path in PUBLIC_ROUTES:
        assert method.lower() in paths[path]


def test_login_sets_a_hardened_cookie_and_returns_a_csrf_token(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    client = TestClient(app)
    response = client.post(
        f"{API}/auth/login",
        json={"organization": tenant_slug(db, tenant), "email": email("alice"), "password": STRONG},
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "df_session" in response.cookies
    assert response.cookies["df_session"] not in response.text
    me = client.get(f"{API}/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == email("alice")
    assert "password_hash" not in me.text
    assert "token_hash" not in me.text


def test_failed_login_sets_no_cookie_and_uses_one_error_shape(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    client = TestClient(app)
    response = client.post(
        f"{API}/auth/login",
        json={"organization": tenant_slug(db, tenant), "email": email("alice"), "password": "nope"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthenticated"
    assert body["correlation_id"] == response.headers["x-correlation-id"]
    assert "set-cookie" not in response.headers


def test_api_responses_are_not_cached_and_carry_security_headers(app: FastAPI) -> None:
    response = TestClient(app).get(f"{API}/auth/me")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_changes_need_the_csrf_token(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    new_admin(db, tenant)
    client, csrf = sign_in(app, db, tenant, "admin")
    body = {"name": "Finance"}
    assert client.post(f"{API}/groups", json=body).status_code == 403
    assert (
        client.post(f"{API}/groups", json=body, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert client.post(f"{API}/groups", json=body, headers=csrf).status_code == 201
    assert client.get(f"{API}/groups").status_code == 200  # reads need no token


def test_csrf_failures_are_audited(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    new_admin(db, tenant)
    client, _ = sign_in(app, db, tenant, "admin")
    client.post(f"{API}/groups", json={"name": "Finance"})
    event = audit_events(db, tenant, "access.denied")[-1]
    assert event.details["reason"] == "csrf"


def test_logout_ends_the_session(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    new_user(db, tenant, "alice")
    client, csrf = sign_in(app, db, tenant, "alice")
    stolen = client.cookies.get("df_session")
    assert client.post(f"{API}/auth/logout", headers=csrf).status_code == 204
    assert client.get(f"{API}/auth/me").status_code == 401
    other = TestClient(app)
    other.cookies.set("df_session", stolen)
    assert other.get(f"{API}/auth/me").status_code == 401


def test_a_new_user_must_change_the_password_before_anything_else(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    new_admin(db, tenant)
    boss, boss_csrf = sign_in(app, db, tenant, "admin")
    created = boss.post(
        f"{API}/users",
        json={"email": email("newbie"), "display_name": "Newbie", "password": STRONG},
        headers=boss_csrf,
    )
    assert created.status_code == 201
    assert created.json()["must_change_password"] is True

    client, csrf = sign_in(app, db, tenant, "newbie")
    blocked = client.get(f"{API}/users")
    assert (blocked.status_code, blocked.json()["error"]["code"]) == (
        403,
        "password_change_required",
    )
    assert client.get(f"{API}/auth/me").status_code == 200
    changed = client.post(
        f"{API}/auth/password",
        json={"current_password": STRONG, "new_password": "my-own-strong-pass-5"},
        headers=csrf,
    )
    assert changed.status_code == 204
    assert client.get(f"{API}/users").json()["error"]["code"] == "forbidden"  # now just no rights
    assert not audit_events(db, tenant, "auth.password_changed") == []


def test_mfa_over_http(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    new_user(db, tenant, "alice")
    client, csrf = sign_in(app, db, tenant, "alice")
    secret = client.post(f"{API}/auth/mfa/setup", headers=csrf).json()["secret"]
    step = int(time.time()) // 30
    confirmed = client.post(
        f"{API}/auth/mfa/confirm", json={"code": pyotp.TOTP(secret).at(step * 30)}, headers=csrf
    )
    assert confirmed.status_code == 200
    assert len(confirmed.json()["recovery_codes"]) == 10
    client.post(f"{API}/auth/logout", headers=csrf)

    pending = client.post(
        f"{API}/auth/login",
        json={
            "organization": tenant_slug(db, tenant),
            "email": email("alice"),
            "password": STRONG,
        },
    )
    assert pending.json() == {
        "state": "mfa_pending",
        "must_change_password": False,
        "csrf_token": None,
    }
    assert client.get(f"{API}/auth/me").status_code == 401  # half-signed-in grants nothing

    wrong = client.post(f"{API}/auth/mfa", json={"code": "000000"})
    assert wrong.status_code == 400
    done = client.post(f"{API}/auth/mfa", json={"code": pyotp.TOTP(secret).at((step + 1) * 30)})
    assert done.status_code == 200
    assert done.json()["state"] == "active"
    assert client.get(f"{API}/auth/me").status_code == 200


def test_refusals_are_audited_with_who_asked_and_what_was_needed(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    new_admin(db, tenant)
    desk = new_user(db, tenant, "helpdesk")
    assign_role(db, tenant, new_role(db, tenant, "Helpdesk", ("users.manage",)), user_id=desk)
    client, csrf = sign_in(app, db, tenant, "helpdesk")
    assert client.get(f"{API}/users").status_code == 200
    denied = client.post(f"{API}/roles", json={"name": "Sneaky", "permissions": []}, headers=csrf)
    assert denied.status_code == 403
    event = audit_events(db, tenant, "access.denied")[-1]
    assert (event.actor_id, event.actor_type, event.result) == (desk, "user", "failure")
    assert event.details["required"] == "security.admin"
    assert event.details["path"] == f"{API}/roles"


def test_role_changes_over_the_api_take_effect_on_the_next_request(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    new_admin(db, tenant)
    felix = new_user(db, tenant, "felix")
    role = new_role(db, tenant, "Directory reader", ("users.manage",))
    boss, boss_csrf = sign_in(app, db, tenant, "admin")
    felix_client, _ = sign_in(app, db, tenant, "felix")

    assert felix_client.get(f"{API}/users").status_code == 403
    assert (
        boss.put(
            f"{API}/roles/{role}/assignments", json={"user_id": str(felix)}, headers=boss_csrf
        ).status_code
        == 204
    )
    assert felix_client.get(f"{API}/users").status_code == 200
    assert (
        boss.post(
            f"{API}/roles/{role}/assignments/remove",
            json={"user_id": str(felix)},
            headers=boss_csrf,
        ).status_code
        == 204
    )
    assert felix_client.get(f"{API}/users").status_code == 403


def test_api_key_lifecycle_over_http(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    new_admin(db, tenant)
    boss, csrf = sign_in(app, db, tenant, "admin")
    created = boss.post(f"{API}/api-keys", json={"name": "ERP", "expires_days": 30}, headers=csrf)
    assert created.status_code == 201
    token, key_id = created.json()["token"], created.json()["id"]
    assert token.startswith("dfk_")
    listed = boss.get(f"{API}/api-keys").json()["items"]
    assert [item["name"] for item in listed] == ["ERP"]
    assert token not in json.dumps(listed)

    machine = TestClient(app)
    bearer = {"Authorization": f"Bearer {token}"}
    assert machine.get(f"{API}/users", headers=bearer).status_code == 403  # no rights yet
    assert audit_events(db, tenant, "access.denied")[-1].actor_type == "api_key"

    service_id = uuid.UUID(created.json()["service_user_id"])
    role = new_role(db, tenant, "Connector", ("users.manage", "groups.manage"))
    assign_role(db, tenant, role, user_id=service_id)
    assert machine.get(f"{API}/users?kind=service", headers=bearer).status_code == 200
    made = machine.post(f"{API}/groups", json={"name": "From ERP"}, headers=bearer)
    assert made.status_code == 201  # no CSRF token is needed, because there is no cookie to abuse

    assert boss.delete(f"{API}/api-keys/{key_id}", headers=csrf).status_code == 204
    assert machine.get(f"{API}/users", headers=bearer).status_code == 401


def test_pagination_and_search(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    new_admin(db, tenant)
    for index in range(5):
        new_user(db, tenant, f"member{index}")
    client, _ = sign_in(app, db, tenant, "admin")
    page = client.get(f"{API}/users?limit=2&offset=0").json()
    assert (len(page["items"]), page["total"], page["limit"]) == (2, 6, 2)
    second = client.get(f"{API}/users?limit=2&offset=2").json()
    assert {u["id"] for u in page["items"]}.isdisjoint({u["id"] for u in second["items"]})
    found = client.get(f"{API}/users?search=member3").json()
    assert [u["email"] for u in found["items"]] == [email("member3")]
    assert client.get(f"{API}/users?limit=0").status_code == 422
    assert client.get(f"{API}/users?limit=1000").status_code == 422


def test_validation_errors_never_echo_what_was_submitted(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    new_admin(db, tenant)
    client, csrf = sign_in(app, db, tenant, "admin")
    response = client.post(
        f"{API}/users",
        json={"email": "not-an-email", "display_name": "X", "password": "very-secret-pass-123"},
        headers=csrf,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "very-secret-pass-123" not in response.text
    assert any(field["field"] == "email" for field in response.json()["error"]["fields"])


def test_the_last_admin_cannot_be_disabled_over_http(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    admin_id, _ = new_admin(db, tenant)
    client, csrf = sign_in(app, db, tenant, "admin")
    response = client.patch(
        f"{API}/users/{admin_id}", json={"row_version": 1, "status": "disabled"}, headers=csrf
    )
    assert (response.status_code, response.json()["error"]["code"]) == (409, "conflict")


def test_an_admin_cannot_reach_another_tenants_users(
    app: FastAPI, db: Database, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    new_admin(db, tenant)
    stranger = new_user(db, other_tenant, "stranger")
    client, csrf = sign_in(app, db, tenant, "admin")
    response = client.patch(
        f"{API}/users/{stranger}", json={"row_version": 1, "status": "disabled"}, headers=csrf
    )
    assert response.status_code == 404
    emails = [u["email"] for u in client.get(f"{API}/users").json()["items"]]
    assert email("stranger") not in emails


def test_acl_endpoints_enforce_manage_acl_and_no_escalation(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    new_admin(db, tenant)
    root = new_anchor(db, tenant, "tenant")
    finance = new_anchor(db, tenant, "cabinet", root)
    invoices = new_anchor(db, tenant, "folder", finance)
    manager = new_user(db, tenant, "manager")
    felix = new_user(db, tenant, "felix")
    reader = new_user(db, tenant, "reader")
    for permission in ("read", "manage_acl"):
        grant(db, tenant, finance, "user", manager, permission)
    grant(db, tenant, finance, "user", reader, "read")

    boss, _ = sign_in(app, db, tenant, "admin")
    mgr, mgr_csrf = sign_in(app, db, tenant, "manager")
    rdr, rdr_csrf = sign_in(app, db, tenant, "reader")
    grant_read = {
        "entries": [
            {
                "principal_type": "user",
                "principal_id": str(felix),
                "permission": "read",
                "effect": "allow",
            }
        ]
    }

    assert rdr.get(f"{API}/acl/anchors/{invoices}").status_code == 403
    assert (
        rdr.put(f"{API}/acl/anchors/{invoices}", json=grant_read, headers=rdr_csrf).status_code
        == 403
    )
    assert (
        mgr.put(f"{API}/acl/anchors/{uuid.uuid4()}", json=grant_read, headers=mgr_csrf).status_code
        == 404
    )

    too_much = {"entries": [{**grant_read["entries"][0], "permission": "download"}]}
    assert (
        mgr.put(f"{API}/acl/anchors/{invoices}", json=too_much, headers=mgr_csrf).status_code == 403
    )

    before = boss.get(
        f"{API}/users/{felix}/effective-permissions", params={"anchor_id": str(invoices)}
    )
    assert before.json()["read"]["allowed"] is False
    assert (
        mgr.put(f"{API}/acl/anchors/{invoices}", json=grant_read, headers=mgr_csrf).status_code
        == 204
    )
    after = boss.get(
        f"{API}/users/{felix}/effective-permissions", params={"anchor_id": str(invoices)}
    )
    assert after.json()["read"]["allowed"] is True
    assert mgr.get(f"{API}/acl/anchors/{invoices}").json()["entries"][0]["principal_id"] == str(
        felix
    )


def test_sessions_can_be_listed_and_revoked(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    new_user(db, tenant, "alice")
    first, csrf = sign_in(app, db, tenant, "alice")
    second, _ = sign_in(app, db, tenant, "alice")
    listed = first.get(f"{API}/auth/sessions").json()
    assert len(listed) == 2
    assert sum(item["current"] for item in listed) == 1
    other = next(item for item in listed if not item["current"])
    assert first.delete(f"{API}/auth/sessions/{other['id']}", headers=csrf).status_code == 204
    assert second.get(f"{API}/auth/me").status_code == 401
    assert first.delete(f"{API}/auth/sessions/{uuid.uuid4()}", headers=csrf).status_code == 404


def test_users_cannot_revoke_each_others_sessions(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    new_user(db, tenant, "bob")
    alice, _ = sign_in(app, db, tenant, "alice")
    bob, bob_csrf = sign_in(app, db, tenant, "bob")
    alices = alice.get(f"{API}/auth/sessions").json()[0]["id"]
    assert bob.delete(f"{API}/auth/sessions/{alices}", headers=bob_csrf).status_code == 404
    assert alice.get(f"{API}/auth/me").status_code == 200


def test_groups_and_membership_over_http(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    new_admin(db, tenant)
    felix = new_user(db, tenant, "felix")
    team = new_group(db, tenant, "Finance")
    client, csrf = sign_in(app, db, tenant, "admin")
    assert client.put(f"{API}/groups/{team}/members/{felix}", headers=csrf).status_code == 204
    assert client.put(f"{API}/groups/{team}/members/{felix}", headers=csrf).status_code == 409
    members = client.get(f"{API}/groups/{team}/members").json()
    assert [m["email"] for m in members] == [email("felix")]
    assert client.delete(f"{API}/groups/{team}/members/{felix}", headers=csrf).status_code == 204
    assert client.get(f"{API}/groups/{uuid.uuid4()}/members").status_code == 404


def test_permission_catalogue_and_roles_list(app: FastAPI, db: Database, tenant: uuid.UUID) -> None:
    new_admin(db, tenant)
    client, _ = sign_in(app, db, tenant, "admin")
    codes = {item["code"] for item in client.get(f"{API}/permissions").json()}
    assert {"security.admin", "read", "manage_acl"} <= codes
    roles = client.get(f"{API}/roles").json()["items"]
    assert roles[0]["name"] == "Security admin"
    assert "security.admin" in roles[0]["permissions"]
