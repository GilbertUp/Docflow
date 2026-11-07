"""F01 permissions against a real database: the Finance and HR demo, and immediate effect."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from docuflow.modules.identity.admin import AclEntryInput, IdentityAdmin
from docuflow.modules.identity.models import AclAnchor, AclEntry
from docuflow.modules.identity.policy import PolicyService
from docuflow.platform.audit import AuditLog
from docuflow.platform.db import Database
from docuflow.platform.errors import DomainError, Forbidden, NotFound
from tests.identity.support import (
    audit_events,
    grant,
    join_group,
    new_admin,
    new_anchor,
    new_group,
    new_role,
    new_user,
    principal,
)

pytestmark = pytest.mark.db


class World:
    """Tenant root, Finance and HR cabinets, and a folder inside Finance."""

    def __init__(self, db: Database, tenant: uuid.UUID) -> None:
        self.root = new_anchor(db, tenant, "tenant")
        self.finance = new_anchor(db, tenant, "cabinet", self.root)
        self.hr = new_anchor(db, tenant, "cabinet", self.root)
        self.invoices = new_anchor(db, tenant, "folder", self.finance)


@pytest.fixture
def world(db: Database, tenant: uuid.UUID) -> World:
    return World(db, tenant)


def can(
    db: Database,
    policy: PolicyService,
    tenant: uuid.UUID,
    user: uuid.UUID,
    anchor: uuid.UUID,
    permission: str = "read",
) -> bool:
    with db.tenant_session(tenant) as session:
        return policy.can(session, tenant, user, anchor, permission)


def readable(
    db: Database, policy: PolicyService, tenant: uuid.UUID, user: uuid.UUID
) -> frozenset[uuid.UUID]:
    with db.tenant_session(tenant) as session:
        return policy.readable_anchors(session, tenant, user)


def test_finance_user_sees_only_finance_and_hr_user_only_hr(
    db: Database, policy: PolicyService, tenant: uuid.UUID, world: World
) -> None:
    finance_team, hr_team = new_group(db, tenant, "Finance"), new_group(db, tenant, "HR")
    felix, hana = new_user(db, tenant, "felix"), new_user(db, tenant, "hana")
    join_group(db, tenant, finance_team, felix)
    join_group(db, tenant, hr_team, hana)
    grant(db, tenant, world.finance, "group", finance_team, "read")
    grant(db, tenant, world.hr, "group", hr_team, "read")

    assert readable(db, policy, tenant, felix) == {world.finance, world.invoices}
    assert readable(db, policy, tenant, hana) == {world.hr}
    assert not can(db, policy, tenant, felix, world.hr)
    assert not can(db, policy, tenant, hana, world.invoices)


def test_users_start_with_no_access(
    db: Database, policy: PolicyService, tenant: uuid.UUID, world: World
) -> None:
    nobody = new_user(db, tenant, "nobody")
    assert readable(db, policy, tenant, nobody) == frozenset()


def test_removing_a_role_changes_access_on_the_very_next_check(
    db: Database,
    policy: PolicyService,
    admin: IdentityAdmin,
    tenant: uuid.UUID,
    world: World,
) -> None:
    admin_id, _ = new_admin(db, tenant)
    boss = principal(tenant, admin_id)
    felix = new_user(db, tenant, "felix")
    reader = new_role(db, tenant, "Finance reader")
    grant(db, tenant, world.finance, "role", reader, "read")

    assert not can(db, policy, tenant, felix, world.invoices)
    with db.tenant_session(tenant) as session:
        admin.assign_role(session, boss, reader, felix, None)
    assert can(db, policy, tenant, felix, world.invoices)
    with db.tenant_session(tenant) as session:
        admin.unassign_role(session, boss, reader, felix, None)
    assert not can(db, policy, tenant, felix, world.invoices)


def test_group_membership_changes_take_effect_immediately(
    db: Database,
    policy: PolicyService,
    admin: IdentityAdmin,
    tenant: uuid.UUID,
    world: World,
) -> None:
    admin_id, _ = new_admin(db, tenant)
    boss = principal(tenant, admin_id)
    team = new_group(db, tenant, "Finance")
    felix = new_user(db, tenant, "felix")
    grant(db, tenant, world.finance, "group", team, "read")

    with db.tenant_session(tenant) as session:
        admin.add_member(session, boss, team, felix)
    assert can(db, policy, tenant, felix, world.finance)
    with db.tenant_session(tenant) as session:
        admin.remove_member(session, boss, team, felix)
    assert not can(db, policy, tenant, felix, world.finance)


def test_disabling_a_user_removes_all_access(
    db: Database,
    policy: PolicyService,
    admin: IdentityAdmin,
    tenant: uuid.UUID,
    world: World,
) -> None:
    admin_id, _ = new_admin(db, tenant)
    felix = new_user(db, tenant, "felix")
    grant(db, tenant, world.finance, "user", felix, "read")
    assert can(db, policy, tenant, felix, world.finance)
    with db.tenant_session(tenant) as session:
        admin.update_user(
            session, principal(tenant, admin_id), felix, row_version=1, status="disabled"
        )
    assert not can(db, policy, tenant, felix, world.finance)


def test_department_entries_apply_to_members(
    db: Database,
    policy: PolicyService,
    admin: IdentityAdmin,
    tenant: uuid.UUID,
    world: World,
) -> None:
    admin_id, _ = new_admin(db, tenant)
    boss = principal(tenant, admin_id)
    felix = new_user(db, tenant, "felix")
    with db.tenant_session(tenant) as session:
        department = admin.create_department(session, boss, "Finance", None)
        department_id = department.id
        admin.update_user(session, boss, felix, row_version=1, department_id=department_id)
    grant(db, tenant, world.finance, "department", department_id, "read")
    assert can(db, policy, tenant, felix, world.finance)


def test_deny_exception_and_nested_allow_exception(
    db: Database, policy: PolicyService, tenant: uuid.UUID, world: World
) -> None:
    sub = new_anchor(db, tenant, "folder", world.invoices)
    team = new_group(db, tenant, "Finance")
    felix = new_user(db, tenant, "felix")
    join_group(db, tenant, team, felix)
    grant(db, tenant, world.finance, "group", team, "read")
    grant(db, tenant, world.invoices, "user", felix, "read", "deny")
    grant(db, tenant, sub, "user", felix, "read")

    assert can(db, policy, tenant, felix, world.finance)
    assert not can(db, policy, tenant, felix, world.invoices)
    assert can(db, policy, tenant, felix, sub)


def test_another_tenants_user_has_no_access_here(
    db: Database,
    policy: PolicyService,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
    world: World,
) -> None:
    felix = new_user(db, tenant, "felix")
    grant(db, tenant, world.finance, "user", felix, "read")
    assert can(db, policy, tenant, felix, world.finance)
    # Asking the other tenant about this tenant's user and anchor reveals and grants nothing.
    assert not can(db, policy, other_tenant, felix, world.finance)
    assert readable(db, policy, other_tenant, felix) == frozenset()


def test_effective_permission_inspector_explains_each_decision(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID, world: World
) -> None:
    felix = new_user(db, tenant, "felix")
    grant(db, tenant, world.finance, "user", felix, "read")
    with db.tenant_session(tenant) as session:
        result = admin.effective_permissions(session, tenant, felix, world.invoices)
    assert result["read"]["allowed"] is True
    assert result["read"]["source_anchor_id"] == world.finance
    assert result["download"]["allowed"] is False
    assert result["download"]["reason"] == "no matching entry"


def test_inspector_does_not_reveal_another_tenants_user(
    db: Database,
    admin: IdentityAdmin,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
    world: World,
) -> None:
    stranger = new_user(db, other_tenant, "stranger")
    with pytest.raises(NotFound), db.tenant_session(tenant) as session:
        admin.effective_permissions(session, tenant, stranger, world.finance)


def manager_setup(db: Database, tenant: uuid.UUID, world: World) -> uuid.UUID:
    """A manager who can read and manage permissions on Finance, but cannot download."""
    manager = new_user(db, tenant, "manager")
    grant(db, tenant, world.finance, "user", manager, "read")
    grant(db, tenant, world.finance, "user", manager, "manage_acl")
    return manager


def anchor_of(session, anchor_id: uuid.UUID) -> AclAnchor:
    anchor = session.get(AclAnchor, anchor_id)
    assert anchor is not None
    return anchor


def test_acl_change_applies_immediately_and_is_audited(
    db: Database,
    policy: PolicyService,
    admin: IdentityAdmin,
    audit: AuditLog,
    tenant: uuid.UUID,
    world: World,
) -> None:
    manager = manager_setup(db, tenant, world)
    felix = new_user(db, tenant, "felix")
    assert not can(db, policy, tenant, felix, world.invoices)
    with db.tenant_session(tenant) as session:
        admin.replace_acl(
            session,
            principal(tenant, manager),
            anchor_of(session, world.invoices),
            [AclEntryInput("user", felix, "read", "allow")],
        )
    assert can(db, policy, tenant, felix, world.invoices)
    event = audit_events(db, tenant, "acl.changed")[-1]
    assert event.actor_id == manager
    assert json.loads(json.dumps(event.details))["after"][0]["permission"] == "read"
    with db.tenant_session(tenant) as session:
        assert audit.verify(session, tenant).ok


def test_nobody_can_grant_access_they_do_not_hold(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID, world: World
) -> None:
    manager = manager_setup(db, tenant, world)
    felix = new_user(db, tenant, "felix")
    with pytest.raises(Forbidden), db.tenant_session(tenant) as session:
        admin.replace_acl(
            session,
            principal(tenant, manager),
            anchor_of(session, world.invoices),
            [AclEntryInput("user", felix, "download", "allow")],
        )


def test_denies_can_be_added_without_holding_the_permission(
    db: Database, policy: PolicyService, admin: IdentityAdmin, tenant: uuid.UUID, world: World
) -> None:
    manager = manager_setup(db, tenant, world)
    felix = new_user(db, tenant, "felix")
    grant(db, tenant, world.finance, "user", felix, "download")
    with db.tenant_session(tenant) as session:
        admin.replace_acl(
            session,
            principal(tenant, manager),
            anchor_of(session, world.invoices),
            [AclEntryInput("user", felix, "download", "deny")],
        )
    assert can(db, policy, tenant, felix, world.finance, "download")
    assert not can(db, policy, tenant, felix, world.invoices, "download")


def test_changing_permissions_needs_manage_acl(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID, world: World
) -> None:
    reader = new_user(db, tenant, "reader")
    grant(db, tenant, world.finance, "user", reader, "read")
    with pytest.raises(Forbidden), db.tenant_session(tenant) as session:
        admin.replace_acl(
            session, principal(tenant, reader), anchor_of(session, world.invoices), []
        )
    with pytest.raises(Forbidden), db.tenant_session(tenant) as session:
        admin.get_acl(session, principal(tenant, reader), anchor_of(session, world.invoices))


def test_failed_acl_change_changes_nothing(
    db: Database, admin: IdentityAdmin, tenant: uuid.UUID, world: World
) -> None:
    manager = manager_setup(db, tenant, world)
    felix = new_user(db, tenant, "felix")
    grant(db, tenant, world.invoices, "user", felix, "read")
    with pytest.raises(Forbidden), db.tenant_session(tenant) as session:
        admin.replace_acl(
            session,
            principal(tenant, manager),
            anchor_of(session, world.invoices),
            [AclEntryInput("user", felix, "download", "allow")],
        )
    with db.tenant_session(tenant) as session:
        remaining = session.scalars(
            select(AclEntry).where(AclEntry.anchor_id == world.invoices)
        ).all()
    assert [(item.permission, item.effect) for item in remaining] == [("read", "allow")]


@pytest.mark.parametrize(
    ("permission", "effect", "expected"),
    [("fly", "allow", DomainError), ("read", "maybe", DomainError)],
)
def test_invalid_acl_entries_are_rejected(
    db: Database,
    admin: IdentityAdmin,
    tenant: uuid.UUID,
    world: World,
    permission: str,
    effect: str,
    expected: type[Exception],
) -> None:
    manager = manager_setup(db, tenant, world)
    with pytest.raises(expected), db.tenant_session(tenant) as session:
        admin.replace_acl(
            session,
            principal(tenant, manager),
            anchor_of(session, world.invoices),
            [AclEntryInput("user", manager, permission, effect)],
        )


def test_acl_entries_cannot_name_another_tenants_principal(
    db: Database,
    admin: IdentityAdmin,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
    world: World,
) -> None:
    manager = manager_setup(db, tenant, world)
    stranger = new_user(db, other_tenant, "stranger")
    with pytest.raises(NotFound), db.tenant_session(tenant) as session:
        admin.replace_acl(
            session,
            principal(tenant, manager),
            anchor_of(session, world.invoices),
            [AclEntryInput("user", stranger, "read", "deny")],
        )
