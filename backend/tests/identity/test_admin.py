"""F01 administration: escalation prevention, last-admin protection, API keys, audit."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from docuflow.modules.identity.admin import IdentityAdmin
from docuflow.modules.identity.auth import AuthService
from docuflow.modules.identity.catalogue import SYSTEM_PERMISSIONS
from docuflow.modules.identity.models import ApiKey, Role, User, UserSession
from docuflow.modules.identity.policy import PolicyService
from docuflow.platform.audit import AuditLog
from docuflow.platform.db import Database, utcnow
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound, Unauthenticated
from tests.identity.support import (
    STRONG,
    assign_role,
    audit_actions,
    audit_events,
    email,
    join_group,
    new_admin,
    new_group,
    new_role,
    new_user,
    principal,
    tenant_slug,
)

pytestmark = pytest.mark.db
HELPDESK = ("users.manage", "groups.manage")


def helpdesk(db: Database, tenant: uuid.UUID) -> uuid.UUID:
    user = new_user(db, tenant, "helpdesk")
    assign_role(db, tenant, new_role(db, tenant, "Helpdesk", HELPDESK), user_id=user)
    return user


def test_admin_creates_a_user_who_must_change_the_password(
    db: Database, admin: IdentityAdmin, auth: AuthService, tenant: uuid.UUID
) -> None:
    boss = principal(tenant, new_admin(db, tenant)[0])
    with db.tenant_session(tenant) as session:
        admin.create_user(session, boss, "Newcomer@Example.com", "Newcomer", STRONG)
    issued = auth.login(tenant_slug(db, tenant), "newcomer@example.com", STRONG)
    assert issued.must_change_password is True
    assert "user.created" in audit_actions(db, tenant)


def test_duplicate_and_weak_users_are_rejected(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID
) -> None:
    boss = principal(tenant, new_admin(db, tenant)[0])
    new_user(db, tenant, "alice")
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        admin.create_user(session, boss, email("alice"), "Alice", STRONG)
    with pytest.raises(DomainError), db.tenant_session(tenant) as session:
        admin.create_user(session, boss, email("bob"), "Bob", "short")


def test_stale_edits_are_rejected(db: Database, admin: IdentityAdmin, tenant: uuid.UUID) -> None:
    boss = principal(tenant, new_admin(db, tenant)[0])
    alice = new_user(db, tenant, "alice")
    with db.tenant_session(tenant) as session:
        admin.update_user(session, boss, alice, row_version=1, display_name="Alice A")
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        admin.update_user(session, boss, alice, row_version=1, display_name="Alice B")


def test_disabling_a_user_ends_their_sessions(
    db: Database, admin: IdentityAdmin, auth: AuthService, tenant: uuid.UUID
) -> None:
    boss = principal(tenant, new_admin(db, tenant)[0])
    alice = new_user(db, tenant, "alice")
    token = auth.login(tenant_slug(db, tenant), email("alice"), STRONG).token
    assert auth.authenticate_session(token)
    with db.tenant_session(tenant) as session:
        admin.update_user(session, boss, alice, row_version=1, status="disabled")
    with pytest.raises(Unauthenticated):
        auth.authenticate_session(token)


def test_password_reset_forces_a_change_and_ends_sessions(
    db: Database, admin: IdentityAdmin, auth: AuthService, tenant: uuid.UUID
) -> None:
    boss = principal(tenant, new_admin(db, tenant)[0])
    alice = new_user(db, tenant, "alice")
    token = auth.login(tenant_slug(db, tenant), email("alice"), STRONG).token
    with db.tenant_session(tenant) as session:
        admin.reset_password(session, boss, alice, "a-brand-new-password-3")
    with pytest.raises(Unauthenticated):
        auth.authenticate_session(token)
    issued = auth.login(tenant_slug(db, tenant), email("alice"), "a-brand-new-password-3")
    assert issued.must_change_password is True


def test_a_role_editor_cannot_assign_a_more_powerful_role(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID
) -> None:
    _, admin_role_id = new_admin(db, tenant)
    desk = principal(tenant, helpdesk(db, tenant))
    target = new_user(db, tenant, "target")
    with pytest.raises(Forbidden), db.tenant_session(tenant) as session:
        admin.assign_role(session, desk, admin_role_id, target, None)


def test_a_role_editor_cannot_create_a_role_with_more_power(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID
) -> None:
    new_admin(db, tenant)
    desk = principal(tenant, helpdesk(db, tenant))
    with pytest.raises(Forbidden), db.tenant_session(tenant) as session:
        admin.create_role(session, desk, "Sneaky", "", ["security.admin"])
    with db.tenant_session(tenant) as session:
        admin.create_role(session, desk, "Within reach", "", ["users.manage"])


def test_a_role_editor_cannot_add_power_to_their_own_role(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID
) -> None:
    new_admin(db, tenant)
    desk = principal(tenant, helpdesk(db, tenant))
    with db.tenant_session(tenant) as session:
        helpdesk_role = session.scalar(select(Role.id).where(Role.name == "Helpdesk"))
    assert helpdesk_role is not None
    with pytest.raises(Forbidden), db.tenant_session(tenant) as session:
        admin.set_role_permissions(session, desk, helpdesk_role, [*HELPDESK, "audit.read"])


def test_adding_someone_to_a_powerful_group_needs_that_power(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID
) -> None:
    _, admin_role_id = new_admin(db, tenant)
    admins = new_group(db, tenant, "Admins")
    assign_role(db, tenant, admin_role_id, group_id=admins)
    desk = principal(tenant, helpdesk(db, tenant))
    target = new_user(db, tenant, "target")
    with pytest.raises(Forbidden), db.tenant_session(tenant) as session:
        admin.add_member(session, desk, admins, target)


def test_system_permission_checks(db: Database, admin: IdentityAdmin, tenant: uuid.UUID) -> None:
    new_admin(db, tenant)
    desk = principal(tenant, helpdesk(db, tenant))
    with db.tenant_session(tenant) as session:
        admin.require_system(session, desk, "users.manage")
    with pytest.raises(Forbidden) as caught, db.tenant_session(tenant) as session:
        admin.require_system(session, desk, "security.admin")
    assert caught.value.details == {"required": "security.admin"}


def test_unknown_permission_codes_are_rejected(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID
) -> None:
    boss = principal(tenant, new_admin(db, tenant)[0])
    with pytest.raises(DomainError), db.tenant_session(tenant) as session:
        admin.create_role(session, boss, "Odd", "", ["rule.the.world"])


def test_the_last_security_admin_cannot_be_disabled(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID
) -> None:
    admin_id, _ = new_admin(db, tenant)
    boss = principal(tenant, admin_id)
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        admin.update_user(session, boss, admin_id, row_version=1, status="disabled")


def test_the_last_security_admin_cannot_lose_the_role(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID
) -> None:
    admin_id, role_id = new_admin(db, tenant)
    boss = principal(tenant, admin_id)
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        admin.unassign_role(session, boss, role_id, admin_id, None)
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        admin.set_role_permissions(session, boss, role_id, ["users.manage"])


def test_the_last_admin_cannot_be_removed_from_an_admin_group(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID
) -> None:
    sole = new_user(db, tenant, "sole")
    admins = new_group(db, tenant, "Admins")

    role = new_role(db, tenant, "Admin", tuple(SYSTEM_PERMISSIONS))
    assign_role(db, tenant, role, group_id=admins)
    join_group(db, tenant, admins, sole)
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        admin.remove_member(session, principal(tenant, sole), admins, sole)


def test_an_admin_can_be_disabled_when_another_remains(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID
) -> None:
    first, role_id = new_admin(db, tenant)
    second = new_user(db, tenant, "second")
    assign_role(db, tenant, role_id, user_id=second)
    with db.tenant_session(tenant) as session:
        admin.update_user(
            session, principal(tenant, second), first, row_version=1, status="disabled"
        )


def test_admin_actions_cannot_touch_another_tenant(
    db: Database,
    admin: IdentityAdmin,
    policy: PolicyService,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
) -> None:
    admin_id, role_id = new_admin(db, tenant)
    boss = principal(tenant, admin_id)
    stranger = new_user(db, other_tenant, "stranger")
    with pytest.raises(NotFound), db.tenant_session(tenant) as session:
        admin.assign_role(session, boss, role_id, stranger, None)
    with pytest.raises(NotFound), db.tenant_session(tenant) as session:
        admin.update_user(session, boss, stranger, row_version=1, status="disabled")
    with pytest.raises(NotFound), db.tenant_session(tenant) as session:
        admin.reset_password(session, boss, stranger, "a-brand-new-password-3")
    with db.tenant_session(other_tenant) as session:
        assert session.get(User, stranger).status == "active"  # type: ignore[union-attr]


def test_every_administrative_change_is_audited_and_the_chain_verifies(
    db: Database, admin: IdentityAdmin, audit: AuditLog, tenant: uuid.UUID
) -> None:
    admin_id, _ = new_admin(db, tenant)
    boss = principal(tenant, admin_id)
    with db.tenant_session(tenant) as session:
        group = admin.create_group(session, boss, "Finance", "")
        role = admin.create_role(session, boss, "Reader", "", ["audit.read"])
        user = admin.create_user(session, boss, email("felix"), "Felix", STRONG)
        admin.add_member(session, boss, group.id, user.id)
        admin.assign_role(session, boss, role.id, user.id, None)
    actions = audit_actions(db, tenant)
    for expected in (
        "group.created",
        "role.created",
        "user.created",
        "group.member_added",
        "role.assigned",
    ):
        assert expected in actions
    assert all(event.actor_id == admin_id for event in audit_events(db, tenant, "role.assigned"))
    with db.tenant_session(tenant) as session:
        assert audit.verify(session, tenant).ok


def test_api_key_lifecycle(
    db: Database, admin: IdentityAdmin, auth: AuthService, policy: PolicyService, tenant: uuid.UUID
) -> None:
    boss = principal(tenant, new_admin(db, tenant)[0])
    with db.tenant_session(tenant) as session:
        key, token = admin.create_api_key(session, boss, "ERP connector", None)
        key_id, service_id, stored = key.id, key.service_user_id, key.key_hash
    assert token.startswith("dfk_")
    assert stored != token
    assert token not in stored

    who = auth.authenticate_api_key(token)
    assert (who.tenant_id, who.user_id, who.kind) == (tenant, service_id, "api_key")
    with db.tenant_session(tenant) as session:
        assert policy.system_permissions(session, tenant, service_id) == set()

    with db.tenant_session(tenant) as session:
        admin.revoke_api_key(session, boss, key_id)
    with pytest.raises(Unauthenticated):
        auth.authenticate_api_key(token)
    assert "api_key.created" in audit_actions(db, tenant)
    assert "api_key.revoked" in audit_actions(db, tenant)


def test_api_keys_expire_and_follow_their_service_user(
    db: Database, admin: IdentityAdmin, auth: AuthService, tenant: uuid.UUID
) -> None:
    boss = principal(tenant, new_admin(db, tenant)[0])
    with db.tenant_session(tenant) as session:
        _, expiring = admin.create_api_key(session, boss, "Short lived", 1)
        key, disabled = admin.create_api_key(session, boss, "Soon disabled", None)
        service_id = key.service_user_id
    assert auth.authenticate_api_key(expiring)
    with db.tenant_session(tenant) as session:
        session.execute(
            update(ApiKey)
            .where(ApiKey.name == "Short lived")
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
    with pytest.raises(Unauthenticated):
        auth.authenticate_api_key(expiring)
    with db.tenant_session(tenant) as session:
        admin.update_user(session, boss, service_id, row_version=1, status="disabled")
    with pytest.raises(Unauthenticated):
        auth.authenticate_api_key(disabled)


def test_tokens_and_keys_are_not_interchangeable(
    db: Database, admin: IdentityAdmin, auth: AuthService, tenant: uuid.UUID
) -> None:
    boss = principal(tenant, new_admin(db, tenant)[0])
    new_user(db, tenant, "alice")
    session_token = auth.login(tenant_slug(db, tenant), email("alice"), STRONG).token
    with db.tenant_session(tenant) as session:
        _, key_token = admin.create_api_key(session, boss, "Connector", None)
    with pytest.raises(Unauthenticated):
        auth.authenticate_api_key(session_token)
    with pytest.raises(Unauthenticated):
        auth.authenticate_session(key_token)
    for junk in ("", "dfk_", "dfk_nothex.secret", "Bearer dfk_x"):
        with pytest.raises(Unauthenticated):
            auth.authenticate_api_key(junk)


def test_service_users_cannot_be_used_to_sign_in(
    db: Database, admin: IdentityAdmin, auth: AuthService, tenant: uuid.UUID
) -> None:
    boss = principal(tenant, new_admin(db, tenant)[0])
    with db.tenant_session(tenant) as session:
        admin.create_api_key(session, boss, "Connector", None)
    with db.tenant_session(tenant) as session:
        service_email = session.scalar(select(User.email).where(User.kind == "service"))
    assert service_email is not None
    with pytest.raises(Unauthenticated):
        auth.login(tenant_slug(db, tenant), service_email, STRONG)


def test_session_listing_shows_only_live_sessions(
    db: Database, admin: IdentityAdmin, auth: AuthService, tenant: uuid.UUID
) -> None:
    alice = new_user(db, tenant, "alice")
    slug = tenant_slug(db, tenant)
    live = auth.login(slug, email("alice"), STRONG).token
    dead = auth.login(slug, email("alice"), STRONG).token
    auth.logout(auth.authenticate_session(dead))
    with db.tenant_session(tenant) as session:
        listed = admin.list_sessions(session, alice)
        assert len(listed) == 1
        assert session.scalar(select(UserSession.id).where(UserSession.revoked_at.is_(None)))
    assert auth.authenticate_session(live)
