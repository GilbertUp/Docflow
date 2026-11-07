"""Read-only listing queries with pagination."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from docuflow.modules.identity.models import (
    ApiKey,
    Department,
    Group,
    GroupMember,
    Role,
    RoleAssignment,
    RolePermission,
    User,
)

MAX_LIMIT = 200


def _page(session: Session, query: Any, limit: int, offset: int) -> tuple[list[Any], int]:
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = list(session.scalars(query.limit(min(limit, MAX_LIMIT)).offset(offset)))
    return rows, int(total or 0)


def list_users(
    session: Session, limit: int, offset: int, search: str | None, kind: str
) -> tuple[list[User], int]:
    query = select(User).where(User.kind == kind).order_by(User.display_name, User.id)
    if search:
        pattern = f"%{search.lower().replace('%', '').replace('_', '')}%"
        query = query.where(
            func.lower(User.email).like(pattern) | func.lower(User.display_name).like(pattern)
        )
    return _page(session, query, limit, offset)


def list_groups(session: Session, limit: int, offset: int) -> tuple[list[Group], int]:
    return _page(session, select(Group).order_by(Group.name), limit, offset)


def list_departments(session: Session, limit: int, offset: int) -> tuple[list[Department], int]:
    return _page(session, select(Department).order_by(Department.name), limit, offset)


def group_members(session: Session, group_id: uuid.UUID) -> list[User]:
    return list(
        session.scalars(
            select(User)
            .join(GroupMember, GroupMember.user_id == User.id)
            .where(GroupMember.group_id == group_id)
            .order_by(User.display_name)
        )
    )


def list_roles(
    session: Session, limit: int, offset: int
) -> tuple[list[tuple[Role, list[str]]], int]:
    roles, total = _page(session, select(Role).order_by(Role.name), limit, offset)
    ids = [role.id for role in roles]
    granted: dict[uuid.UUID, list[str]] = {role_id: [] for role_id in ids}
    for role_id, permission in session.execute(
        select(RolePermission.role_id, RolePermission.permission)
        .where(RolePermission.role_id.in_(ids))
        .order_by(RolePermission.permission)
    ):
        granted[role_id].append(permission)
    return [(role, granted[role.id]) for role in roles], total


def role_holders(session: Session, role_id: uuid.UUID) -> tuple[list[User], list[Group]]:
    users = session.scalars(
        select(User)
        .join(RoleAssignment, RoleAssignment.user_id == User.id)
        .where(RoleAssignment.role_id == role_id)
        .order_by(User.display_name)
    )
    groups = session.scalars(
        select(Group)
        .join(RoleAssignment, RoleAssignment.group_id == Group.id)
        .where(RoleAssignment.role_id == role_id)
        .order_by(Group.name)
    )
    return list(users), list(groups)


def list_api_keys(session: Session, limit: int, offset: int) -> tuple[list[ApiKey], int]:
    return _page(session, select(ApiKey).order_by(ApiKey.created_at.desc()), limit, offset)
