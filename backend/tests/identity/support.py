"""Helpers that build identity data directly, bypassing the services under test."""

from __future__ import annotations

import time
import uuid

import pyotp
from sqlalchemy import select

from docuflow.modules.identity import security
from docuflow.modules.identity.auth import AuthService, Principal
from docuflow.modules.identity.catalogue import SYSTEM_PERMISSIONS
from docuflow.modules.identity.models import (
    AclAnchor,
    AclEntry,
    Group,
    GroupMember,
    Role,
    RoleAssignment,
    RolePermission,
    User,
)
from docuflow.modules.identity.policy import bump_permission_epoch
from docuflow.platform.db import Database
from docuflow.platform.models import AuditEvent, Tenant

STRONG = "correct-horse-battery-9"


def tenant_slug(db: Database, tenant: uuid.UUID) -> str:
    with db.platform_session() as session:
        tenant_row = session.get(Tenant, tenant)
        assert tenant_row is not None
        return tenant_row.slug


def email(name: str) -> str:
    return f"{name}@example.com"


def new_user(
    db: Database, tenant: uuid.UUID, name: str, *, password: str = STRONG, status: str = "active"
) -> uuid.UUID:
    with db.tenant_session(tenant) as session:
        user = User(
            tenant_id=tenant,
            email=email(name),
            display_name=name,
            password_hash=security.hash_password(password),
            status=status,
        )
        session.add(user)
        session.flush()
        bump_permission_epoch(session, tenant)
        return user.id


def new_role(
    db: Database, tenant: uuid.UUID, name: str, permissions: tuple[str, ...] = ()
) -> uuid.UUID:
    with db.tenant_session(tenant) as session:
        role = Role(tenant_id=tenant, name=name)
        session.add(role)
        session.flush()
        session.add_all(
            RolePermission(tenant_id=tenant, role_id=role.id, permission=item)
            for item in permissions
        )
        bump_permission_epoch(session, tenant)
        return role.id


def admin_role(db: Database, tenant: uuid.UUID) -> uuid.UUID:
    return new_role(db, tenant, "Security admin", tuple(SYSTEM_PERMISSIONS))


def new_group(db: Database, tenant: uuid.UUID, name: str) -> uuid.UUID:
    with db.tenant_session(tenant) as session:
        group = Group(tenant_id=tenant, name=name)
        session.add(group)
        session.flush()
        bump_permission_epoch(session, tenant)
        return group.id


def assign_role(
    db: Database,
    tenant: uuid.UUID,
    role_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
) -> None:
    with db.tenant_session(tenant) as session:
        session.add(
            RoleAssignment(tenant_id=tenant, role_id=role_id, user_id=user_id, group_id=group_id)
        )
        bump_permission_epoch(session, tenant)


def join_group(db: Database, tenant: uuid.UUID, group_id: uuid.UUID, user_id: uuid.UUID) -> None:
    with db.tenant_session(tenant) as session:
        session.add(GroupMember(tenant_id=tenant, group_id=group_id, user_id=user_id))
        bump_permission_epoch(session, tenant)


def new_admin(db: Database, tenant: uuid.UUID, name: str = "admin") -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (user id, admin role id). A second admin reuses the role by calling assign_role."""
    role_id = admin_role(db, tenant)
    user_id = new_user(db, tenant, name)
    assign_role(db, tenant, role_id, user_id=user_id)
    return user_id, role_id


def principal(tenant: uuid.UUID, user_id: uuid.UUID) -> Principal:
    return Principal(tenant, user_id, "session")


def new_anchor(
    db: Database, tenant: uuid.UUID, kind: str, parent: uuid.UUID | None = None
) -> uuid.UUID:
    with db.tenant_session(tenant) as session:
        anchor = AclAnchor(
            tenant_id=tenant, resource_type=kind, resource_id=uuid.uuid4(), parent_anchor_id=parent
        )
        session.add(anchor)
        session.flush()
        bump_permission_epoch(session, tenant)
        return anchor.id


def grant(
    db: Database,
    tenant: uuid.UUID,
    anchor: uuid.UUID,
    principal_type: str,
    principal_id: uuid.UUID,
    permission: str,
    effect: str = "allow",
) -> None:
    with db.tenant_session(tenant) as session:
        session.add(
            AclEntry(
                tenant_id=tenant,
                anchor_id=anchor,
                principal_type=principal_type,
                principal_id=principal_id,
                permission=permission,
                effect=effect,
            )
        )
        bump_permission_epoch(session, tenant)


def audit_events(db: Database, tenant: uuid.UUID, action: str | None = None) -> list[AuditEvent]:
    with db.tenant_session(tenant) as session:
        query = select(AuditEvent).order_by(AuditEvent.seq)
        if action:
            query = query.where(AuditEvent.action == action)
        return list(session.scalars(query))


def audit_actions(db: Database, tenant: uuid.UUID) -> list[str]:
    return [event.action for event in audit_events(db, tenant)]


def enable_mfa(auth: AuthService, who: Principal) -> tuple[str, list[str], int, str]:
    """Returns (secret, recovery codes, TOTP step used, the code used)."""
    setup = auth.begin_mfa_setup(who)
    step = int(time.time()) // 30
    code = pyotp.TOTP(setup.secret).at(step * 30)
    return setup.secret, auth.confirm_mfa(who, code), step, code
