"""Permission resolution (ADR 0004).

Every path that reads a resource uses this module: API checks, list filters, and later search,
OCR text, previews and AI retrieval. The rules are a pure function, so they can be tested
exhaustively without a database.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from threading import Lock

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from docuflow.modules.identity.models import (
    AclAnchor,
    AclEntry,
    GroupMember,
    RoleAssignment,
    RolePermission,
    User,
)
from docuflow.platform.models import Tenant

PrincipalKey = tuple[str, uuid.UUID]
CACHE_SIZE = 2000


@dataclass(frozen=True)
class AnchorEntry:
    anchor_id: uuid.UUID
    principal: PrincipalKey
    permission: str
    effect: str


@dataclass(frozen=True)
class Decision:
    allowed: bool
    source_anchor_id: uuid.UUID | None
    reason: str


class AnchorTree:
    """Anchors and the entries that apply to one user's principals."""

    def __init__(
        self, parents: dict[uuid.UUID, uuid.UUID | None], entries: Iterable[AnchorEntry]
    ) -> None:
        self._parents = parents
        self._by_anchor: dict[tuple[uuid.UUID, str], set[str]] = {}
        for entry in entries:
            self._by_anchor.setdefault((entry.anchor_id, entry.permission), set()).add(entry.effect)

    def decide(self, anchor_id: uuid.UUID, permission: str) -> Decision:
        """Nearest level with a matching entry wins; at one level, deny beats allow."""
        current: uuid.UUID | None = anchor_id
        visited: set[uuid.UUID] = set()
        while current is not None and current not in visited:
            visited.add(current)
            effects = self._by_anchor.get((current, permission))
            if effects:
                if "deny" in effects:
                    return Decision(False, current, "denied here")
                return Decision(True, current, "allowed here")
            current = self._parents.get(current)
        return Decision(False, None, "no matching entry")

    def allowed_anchors(self, permission: str) -> frozenset[uuid.UUID]:
        return frozenset(
            anchor for anchor in self._parents if self.decide(anchor, permission).allowed
        )


def bump_permission_epoch(session: Session, tenant_id: uuid.UUID) -> None:
    """Called in the same transaction as any ACL, role or membership change.

    Cached decisions are keyed by this value, so old decisions can never be used after commit.
    """
    session.execute(
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(permission_epoch=Tenant.permission_epoch + 1)
    )


def user_principals(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> set[PrincipalKey]:
    user = session.get(User, user_id)
    if user is None or user.tenant_id != tenant_id or user.status != "active":
        return set()
    principals: set[PrincipalKey] = {("user", user_id)}
    if user.department_id:
        principals.add(("department", user.department_id))
    groups = set(
        session.scalars(select(GroupMember.group_id).where(GroupMember.user_id == user_id))
    )
    principals.update(("group", group) for group in groups)
    roles = session.scalars(
        select(RoleAssignment.role_id).where(
            (RoleAssignment.user_id == user_id) | RoleAssignment.group_id.in_(groups)
        )
    )
    principals.update(("role", role) for role in roles)
    return principals


def load_tree(session: Session, tenant_id: uuid.UUID, principals: set[PrincipalKey]) -> AnchorTree:
    parents = dict(
        session.execute(
            select(AclAnchor.id, AclAnchor.parent_anchor_id).where(AclAnchor.tenant_id == tenant_id)
        )
        .tuples()
        .all()
    )
    ids = {principal_id for _, principal_id in principals}
    rows = session.scalars(
        select(AclEntry).where(AclEntry.tenant_id == tenant_id, AclEntry.principal_id.in_(ids))
    )
    entries = [
        AnchorEntry(
            row.anchor_id, (row.principal_type, row.principal_id), row.permission, row.effect
        )
        for row in rows
        if (row.principal_type, row.principal_id) in principals
    ]
    return AnchorTree(parents, entries)


class PolicyService:
    """Per-process cache in front of the rules. The key includes the tenant's epoch."""

    def __init__(self, size: int = CACHE_SIZE) -> None:
        self._size = size
        self._cache: OrderedDict[tuple[uuid.UUID, uuid.UUID, int], AnchorTree] = OrderedDict()
        self._lock = Lock()

    def tree(self, session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> AnchorTree:
        epoch = session.scalar(select(Tenant.permission_epoch).where(Tenant.id == tenant_id))
        key = (tenant_id, user_id, epoch or 0)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        tree = load_tree(session, tenant_id, user_principals(session, tenant_id, user_id))
        with self._lock:
            self._cache[key] = tree
            while len(self._cache) > self._size:
                self._cache.popitem(last=False)
        return tree

    def can(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        anchor_id: uuid.UUID,
        permission: str,
    ) -> bool:
        return self.tree(session, tenant_id, user_id).decide(anchor_id, permission).allowed

    def readable_anchors(
        self, session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, permission: str = "read"
    ) -> frozenset[uuid.UUID]:
        """The value used to filter queries, so unauthorized rows are never fetched."""
        return self.tree(session, tenant_id, user_id).allowed_anchors(permission)

    def system_permissions(
        self, session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> set[str]:
        """Not cached: administrative checks are rare and must reflect the latest state."""
        principals = user_principals(session, tenant_id, user_id)
        role_ids = [key for kind, key in principals if kind == "role"]
        if not role_ids:
            return set()
        return set(
            session.scalars(
                select(RolePermission.permission).where(RolePermission.role_id.in_(role_ids))
            )
        )
