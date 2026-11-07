"""User, group, role and ACL administration.

Every change is audited, and any change that could alter access bumps the permission epoch in the
same transaction, so a role change takes effect on the very next request.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docuflow.modules.identity import security
from docuflow.modules.identity.auth import API_KEY_PREFIX, AuthService, Principal
from docuflow.modules.identity.catalogue import RESOURCE_PERMISSIONS, SYSTEM_PERMISSIONS
from docuflow.modules.identity.models import (
    AclAnchor,
    AclEntry,
    ApiKey,
    Department,
    Group,
    GroupMember,
    Role,
    RoleAssignment,
    RolePermission,
    User,
    UserSession,
)
from docuflow.modules.identity.policy import (
    PolicyService,
    bump_permission_epoch,
    load_tree,
    user_principals,
)
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import utcnow
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound

SECURITY_ADMIN = "security.admin"


@dataclass(frozen=True)
class AclEntryInput:
    principal_type: str
    principal_id: uuid.UUID
    permission: str
    effect: str


class IdentityAdmin:
    def __init__(self, audit: AuditLog, policy: PolicyService, auth: AuthService) -> None:
        self._audit = audit
        self._policy = policy
        self._auth = auth

    def _record(
        self,
        session: Session,
        principal: Principal,
        action: str,
        object_type: str,
        object_id: object,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._audit.record(
            session,
            principal.tenant_id,
            action,
            actor=AuditActor(principal.user_id, "user"),
            object_type=object_type,
            object_id=str(object_id),
            details=details,
        )

    def require_system(self, session: Session, principal: Principal, permission: str) -> None:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        if permission not in held:
            self._record(
                session,
                principal,
                "access.denied",
                "system_permission",
                permission,
            )
            raise Forbidden("You do not have permission to do that.")

    def _get[T](self, session: Session, model: type[T], item_id: uuid.UUID, label: str) -> T:
        # The tenant session means another tenant's id simply is not found.
        item = session.get(model, item_id)
        if item is None:
            raise NotFound(f"{label} not found.")
        return item

    # Users

    def create_user(
        self,
        session: Session,
        principal: Principal,
        email: str,
        display_name: str,
        password: str,
        department_id: uuid.UUID | None = None,
    ) -> User:
        email = email.strip().lower()
        security.validate_password(password, email)
        if department_id:
            self._get(session, Department, department_id, "Department")
        if session.scalar(select(User.id).where(User.email == email)):
            raise Conflict("A user with that email already exists.")
        user = User(
            tenant_id=principal.tenant_id,
            email=email,
            display_name=display_name,
            password_hash=security.hash_password(password),
            must_change_password=True,
            department_id=department_id,
        )
        session.add(user)
        session.flush()
        bump_permission_epoch(session, principal.tenant_id)
        self._record(session, principal, "user.created", "user", user.id, {"email": email})
        return user

    def update_user(
        self,
        session: Session,
        principal: Principal,
        user_id: uuid.UUID,
        *,
        row_version: int,
        display_name: str | None = None,
        status: str | None = None,
        department_id: uuid.UUID | None = None,
        clear_department: bool = False,
    ) -> User:
        user = self._lock_user(session, user_id)
        if user.row_version != row_version:
            raise Conflict("This user was changed by someone else. Reload and try again.")
        before = {"status": user.status, "department_id": user.department_id}
        if status is not None and status != user.status:
            if status == "disabled":
                self._ensure_not_last_admin(session, user.id)
                self._auth.revoke_all(session, user.id)
            user.status = status
        if display_name is not None:
            user.display_name = display_name
        if department_id is not None:
            self._get(session, Department, department_id, "Department")
            user.department_id = department_id
        elif clear_department:
            user.department_id = None
        user.row_version += 1
        bump_permission_epoch(session, principal.tenant_id)
        self._record(
            session,
            principal,
            "user.updated",
            "user",
            user.id,
            {
                "before": before,
                "after": {"status": user.status, "department_id": user.department_id},
            },
        )
        return user

    def reset_password(
        self, session: Session, principal: Principal, user_id: uuid.UUID, password: str
    ) -> None:
        user = self._lock_user(session, user_id)
        security.validate_password(password, user.email)
        user.password_hash = security.hash_password(password)
        user.must_change_password = True
        user.failed_logins = 0
        user.locked_until = None
        self._auth.revoke_all(session, user.id)
        self._record(session, principal, "user.password_reset", "user", user.id)

    def _lock_user(self, session: Session, user_id: uuid.UUID) -> User:
        user = session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            raise NotFound("User not found.")
        return user

    def _admins(self, session: Session) -> set[uuid.UUID]:
        roles = select(RolePermission.role_id).where(RolePermission.permission == SECURITY_ADMIN)
        direct = session.scalars(
            select(RoleAssignment.user_id).where(
                RoleAssignment.role_id.in_(roles), RoleAssignment.user_id.is_not(None)
            )
        )
        through_groups = session.scalars(
            select(GroupMember.user_id)
            .join(RoleAssignment, RoleAssignment.group_id == GroupMember.group_id)
            .where(RoleAssignment.role_id.in_(roles))
        )
        candidates = {item for item in direct if item} | set(through_groups)
        if not candidates:
            return set()
        return set(
            session.scalars(select(User.id).where(User.id.in_(candidates), User.status == "active"))
        )

    def _ensure_not_last_admin(self, session: Session, user_id: uuid.UUID | None) -> None:
        if user_id is None or self._admins(session) == {user_id}:
            raise Conflict("A tenant must keep at least one security administrator.")

    def _check_admin_remains(self, session: Session) -> None:
        """Run after a change, inside its transaction, so a failing change rolls back."""
        session.flush()
        if not self._admins(session):
            raise Conflict("A tenant must keep at least one security administrator.")

    # Departments and groups

    def create_department(
        self, session: Session, principal: Principal, name: str, parent_id: uuid.UUID | None
    ) -> Department:
        if parent_id:
            self._get(session, Department, parent_id, "Department")
        department = Department(tenant_id=principal.tenant_id, name=name, parent_id=parent_id)
        session.add(department)
        self._flush_unique(session, "A department with that name already exists.")
        self._record(session, principal, "department.created", "department", department.id)
        return department

    def create_group(
        self, session: Session, principal: Principal, name: str, description: str
    ) -> Group:
        group = Group(tenant_id=principal.tenant_id, name=name, description=description)
        session.add(group)
        self._flush_unique(session, "A group with that name already exists.")
        self._record(session, principal, "group.created", "group", group.id)
        return group

    def add_member(
        self, session: Session, principal: Principal, group_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        self._get(session, Group, group_id, "Group")
        self._get(session, User, user_id, "User")
        self._forbid_group_escalation(session, principal, group_id)
        session.add(GroupMember(tenant_id=principal.tenant_id, group_id=group_id, user_id=user_id))
        self._flush_unique(session, "That user is already in the group.")
        bump_permission_epoch(session, principal.tenant_id)
        self._record(session, principal, "group.member_added", "group", group_id, {"user": user_id})

    def remove_member(
        self, session: Session, principal: Principal, group_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        member = session.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.user_id == user_id
            )
        )
        if member is None:
            raise NotFound("That user is not in the group.")
        self._forbid_group_escalation(session, principal, group_id)
        session.delete(member)
        self._check_admin_remains(session)
        bump_permission_epoch(session, principal.tenant_id)
        self._record(
            session, principal, "group.member_removed", "group", group_id, {"user": user_id}
        )

    def _forbid_group_escalation(
        self, session: Session, principal: Principal, group_id: uuid.UUID
    ) -> None:
        roles = session.scalars(
            select(RoleAssignment.role_id).where(RoleAssignment.group_id == group_id)
        )
        for role_id in roles:
            self._forbid_role_escalation(session, principal, role_id)

    # Roles

    def create_role(
        self,
        session: Session,
        principal: Principal,
        name: str,
        description: str,
        permissions: list[str],
    ) -> Role:
        self._validate_system_permissions(permissions)
        role = Role(tenant_id=principal.tenant_id, name=name, description=description)
        session.add(role)
        self._flush_unique(session, "A role with that name already exists.")
        session.add_all(
            RolePermission(tenant_id=principal.tenant_id, role_id=role.id, permission=item)
            for item in set(permissions)
        )
        self._record(
            session, principal, "role.created", "role", role.id, {"permissions": permissions}
        )
        return role

    def set_role_permissions(
        self, session: Session, principal: Principal, role_id: uuid.UUID, permissions: list[str]
    ) -> None:
        self._get(session, Role, role_id, "Role")
        self._validate_system_permissions(permissions)
        current = list(
            session.scalars(select(RolePermission).where(RolePermission.role_id == role_id))
        )
        before = sorted(item.permission for item in current)
        for item in current:
            session.delete(item)
        session.flush()
        session.add_all(
            RolePermission(tenant_id=principal.tenant_id, role_id=role_id, permission=item)
            for item in set(permissions)
        )
        self._check_admin_remains(session)
        bump_permission_epoch(session, principal.tenant_id)
        self._record(
            session,
            principal,
            "role.permissions_changed",
            "role",
            role_id,
            {"before": before, "after": sorted(set(permissions))},
        )

    @staticmethod
    def _validate_system_permissions(permissions: list[str]) -> None:
        unknown = set(permissions) - set(SYSTEM_PERMISSIONS)
        if unknown:
            raise DomainError(f"Unknown permissions: {', '.join(sorted(unknown))}.")

    def _forbid_role_escalation(
        self, session: Session, principal: Principal, role_id: uuid.UUID
    ) -> None:
        """Nobody can hand out a permission they do not hold themselves."""
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        granted = set(
            session.scalars(
                select(RolePermission.permission).where(RolePermission.role_id == role_id)
            )
        )
        if granted - held:
            raise Forbidden("You cannot assign a role with more access than your own.")

    def assign_role(
        self,
        session: Session,
        principal: Principal,
        role_id: uuid.UUID,
        user_id: uuid.UUID | None,
        group_id: uuid.UUID | None,
    ) -> None:
        self._get(session, Role, role_id, "Role")
        self._check_holder(session, user_id, group_id)
        self._forbid_role_escalation(session, principal, role_id)
        session.add(
            RoleAssignment(
                tenant_id=principal.tenant_id, role_id=role_id, user_id=user_id, group_id=group_id
            )
        )
        self._flush_unique(session, "That role is already assigned.")
        bump_permission_epoch(session, principal.tenant_id)
        self._record(
            session,
            principal,
            "role.assigned",
            "role",
            role_id,
            {"user": user_id, "group": group_id},
        )

    def unassign_role(
        self,
        session: Session,
        principal: Principal,
        role_id: uuid.UUID,
        user_id: uuid.UUID | None,
        group_id: uuid.UUID | None,
    ) -> None:
        self._check_holder(session, user_id, group_id)
        self._forbid_role_escalation(session, principal, role_id)
        assignment = session.scalar(
            select(RoleAssignment).where(
                RoleAssignment.role_id == role_id,
                RoleAssignment.user_id == user_id,
                RoleAssignment.group_id == group_id,
            )
        )
        if assignment is None:
            raise NotFound("That role is not assigned.")
        session.delete(assignment)
        self._check_admin_remains(session)
        bump_permission_epoch(session, principal.tenant_id)
        self._record(
            session,
            principal,
            "role.unassigned",
            "role",
            role_id,
            {"user": user_id, "group": group_id},
        )

    def _check_holder(
        self, session: Session, user_id: uuid.UUID | None, group_id: uuid.UUID | None
    ) -> None:
        if (user_id is None) == (group_id is None):
            raise DomainError("Choose either a user or a group.")
        if user_id:
            self._get(session, User, user_id, "User")
        if group_id:
            self._get(session, Group, group_id, "Group")

    # ACLs

    def get_acl(self, session: Session, principal: Principal, anchor: AclAnchor) -> list[AclEntry]:
        self.require_anchor(session, principal, anchor.id, "manage_acl")
        return list(session.scalars(select(AclEntry).where(AclEntry.anchor_id == anchor.id)))

    def replace_acl(
        self,
        session: Session,
        principal: Principal,
        anchor: AclAnchor,
        entries: list[AclEntryInput],
    ) -> None:
        self.require_anchor(session, principal, anchor.id, "manage_acl")
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        seen: set[tuple[str, uuid.UUID, str]] = set()
        for entry in entries:
            if entry.permission not in RESOURCE_PERMISSIONS:
                raise DomainError(f"Unknown permission: {entry.permission}.")
            if entry.effect not in ("allow", "deny"):
                raise DomainError("Effect must be allow or deny.")
            key = (entry.principal_type, entry.principal_id, entry.permission)
            if key in seen:
                raise DomainError(f"Duplicate entry for {entry.permission}.")
            seen.add(key)
            self._check_principal(session, entry.principal_type, entry.principal_id)
            if entry.effect == "allow" and not tree.decide(anchor.id, entry.permission).allowed:
                raise Forbidden(f"You cannot grant {entry.permission}, because you do not hold it.")
        current = list(session.scalars(select(AclEntry).where(AclEntry.anchor_id == anchor.id)))
        before = [_entry_view(item) for item in current]
        for item in current:
            session.delete(item)
        session.flush()
        session.add_all(
            AclEntry(
                tenant_id=principal.tenant_id,
                anchor_id=anchor.id,
                principal_type=entry.principal_type,
                principal_id=entry.principal_id,
                permission=entry.permission,
                effect=entry.effect,
            )
            for entry in entries
        )
        bump_permission_epoch(session, principal.tenant_id)
        self._record(
            session,
            principal,
            "acl.changed",
            anchor.resource_type,
            anchor.resource_id,
            {"before": before, "after": [entry.__dict__ for entry in entries]},
        )

    def _check_principal(self, session: Session, kind: str, item_id: uuid.UUID) -> None:
        models: dict[str, Any] = {
            "user": User,
            "group": Group,
            "role": Role,
            "department": Department,
        }
        model = models.get(kind)
        if model is None:
            raise DomainError(f"Unknown principal type: {kind}.")
        self._get(session, model, item_id, kind.capitalize())

    def require_anchor(
        self, session: Session, principal: Principal, anchor_id: uuid.UUID, permission: str
    ) -> None:
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        if not tree.decide(anchor_id, permission).allowed:
            raise Forbidden("You do not have permission to do that.")

    def effective_permissions(
        self, session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, anchor_id: uuid.UUID
    ) -> dict[str, dict[str, Any]]:
        """Explains each decision for the inspector. Reads fresh data, bypassing the cache."""
        self._get(session, User, user_id, "User")
        tree = load_tree(session, tenant_id, user_principals(session, tenant_id, user_id))
        result: dict[str, dict[str, Any]] = {}
        for permission in RESOURCE_PERMISSIONS:
            decision = tree.decide(anchor_id, permission)
            result[permission] = {
                "allowed": decision.allowed,
                "reason": decision.reason,
                "source_anchor_id": decision.source_anchor_id,
            }
        return result

    # API keys

    def create_api_key(
        self, session: Session, principal: Principal, name: str, expires_days: int | None
    ) -> tuple[ApiKey, str]:
        """Creates a service identity. The key has no access until roles or ACLs grant it."""
        service = User(
            tenant_id=principal.tenant_id,
            email=f"service-{uuid.uuid4().hex[:12]}@service.invalid",
            display_name=name,
            kind="service",
        )
        session.add(service)
        session.flush()
        token = security.make_token(principal.tenant_id, API_KEY_PREFIX)
        key = ApiKey(
            tenant_id=principal.tenant_id,
            name=name,
            prefix=token[:12],
            key_hash=security.hash_token(token),
            service_user_id=service.id,
            created_by=principal.user_id,
            expires_at=utcnow() + timedelta(days=expires_days) if expires_days else None,
        )
        session.add(key)
        session.flush()
        self._record(session, principal, "api_key.created", "api_key", key.id, {"name": name})
        return key, token

    def revoke_api_key(self, session: Session, principal: Principal, key_id: uuid.UUID) -> None:
        key = self._get(session, ApiKey, key_id, "API key")
        key.revoked_at = key.revoked_at or utcnow()
        self._record(session, principal, "api_key.revoked", "api_key", key.id)

    # Sessions

    def list_sessions(self, session: Session, user_id: uuid.UUID) -> list[UserSession]:
        return list(
            session.scalars(
                select(UserSession)
                .where(
                    UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > utcnow(),
                )
                .order_by(UserSession.created_at.desc())
            )
        )

    @staticmethod
    def count(session: Session, model: Any) -> int:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)

    @staticmethod
    def _flush_unique(session: Session, message: str) -> None:
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError as exc:
            raise Conflict(message) from exc


def _entry_view(entry: AclEntry) -> dict[str, Any]:
    return {
        "principal_type": entry.principal_type,
        "principal_id": str(entry.principal_id),
        "permission": entry.permission,
        "effect": entry.effect,
    }
