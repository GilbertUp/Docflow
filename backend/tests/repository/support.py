"""Builders for repository tests. They go through the service, so the rules stay under test."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.models import AclAnchor
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.models import Cabinet, Document, Folder
from docuflow.modules.repository.service import Destination, RepositoryService
from docuflow.platform.db import Database
from docuflow.platform.errors import NotFound
from tests.identity.support import assign_role, grant, new_role, new_user, principal


def boss_of(db: Database, tenant: uuid.UUID, name: str = "boss") -> Principal:
    """A user allowed to create cabinets. Creating one makes them its owner."""
    user = new_user(db, tenant, name)
    role = new_role(db, tenant, f"Cabinet admin {name}", ("cabinets.manage",))
    assign_role(db, tenant, role, user_id=user)
    return principal(tenant, user)


def in_cabinet(cabinet_id: uuid.UUID) -> Destination:
    return Destination(cabinet_id=cabinet_id)


def in_folder(folder_id: uuid.UUID) -> Destination:
    return Destination(folder_id=folder_id)


def make_cabinet(
    db: Database, repo: RepositoryService, who: Principal, name: str, description: str = ""
) -> uuid.UUID:
    with db.tenant_session(who.tenant_id) as session:
        return repo.create_cabinet(session, who, name, description).id


def make_folder(
    db: Database, repo: RepositoryService, who: Principal, where: Destination, name: str
) -> uuid.UUID:
    with db.tenant_session(who.tenant_id) as session:
        return repo.create_folder(session, who, where, name).id


def make_document(
    db: Database,
    repo: RepositoryService,
    who: Principal,
    where: Destination,
    title: str,
    size: int = 0,
) -> uuid.UUID:
    with db.tenant_session(who.tenant_id) as session:
        return repo.file_document(session, who, where, title, size).id


def folder_row(db: Database, tenant: uuid.UUID, folder_id: uuid.UUID) -> Folder:
    with db.tenant_session(tenant) as session:
        row = session.get(Folder, folder_id)
        assert row is not None
        return row


def document_row(db: Database, tenant: uuid.UUID, document_id: uuid.UUID) -> Document:
    with db.tenant_session(tenant) as session:
        row = session.get(Document, document_id)
        assert row is not None
        return row


def cabinet_anchor(db: Database, tenant: uuid.UUID, cabinet_id: uuid.UUID) -> uuid.UUID:
    with db.tenant_session(tenant) as session:
        cabinet = session.get(Cabinet, cabinet_id)
        assert cabinet is not None
        return cabinet.acl_anchor_id


def folder_anchor(db: Database, tenant: uuid.UUID, folder_id: uuid.UUID) -> uuid.UUID:
    return folder_row(db, tenant, folder_id).acl_anchor_id


def allow(
    db: Database,
    tenant: uuid.UUID,
    anchor: uuid.UUID,
    user: uuid.UUID,
    *permissions: str,
    effect: str = "allow",
) -> None:
    for permission in permissions:
        grant(db, tenant, anchor, "user", user, permission, effect)


def sees_folder(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, user: uuid.UUID, folder_id: uuid.UUID
) -> bool:
    try:
        with db.tenant_session(tenant) as session:
            repo.get_folder(session, principal(tenant, user), folder_id)
    except NotFound:
        return False
    return True


def sees_document(
    db: Database,
    repo: RepositoryService,
    tenant: uuid.UUID,
    user: uuid.UUID,
    document_id: uuid.UUID,
) -> bool:
    try:
        with db.tenant_session(tenant) as session:
            repo.get_document(session, principal(tenant, user), document_id)
    except NotFound:
        return False
    return True


def anchor_exists(db: Database, tenant: uuid.UUID, anchor_id: uuid.UUID) -> bool:
    with db.tenant_session(tenant) as session:
        return session.scalar(select(AclAnchor.id).where(AclAnchor.id == anchor_id)) is not None


__all__ = ["PolicyService"]
