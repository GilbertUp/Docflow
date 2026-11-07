"""Cabinets, folders and document filing.

Rules that hold throughout:
- Every operation checks the caller's permission on the exact anchor involved. An item the caller
  cannot read is reported as not found, so its existence is not revealed.
- All structural writes in one organization are serialized by an advisory lock. Two moves racing
  to create a loop therefore cannot both succeed, and deleting a folder cannot race a new child.
- The anchor tree mirrors the folder tree. A move re-parents one anchor, and inherited access
  changes for the whole subtree at once.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.catalogue import RESOURCE_PERMISSIONS
from docuflow.modules.identity.models import AclAnchor, AclEntry
from docuflow.modules.identity.policy import AnchorTree, PolicyService, bump_permission_epoch
from docuflow.modules.repository.models import Cabinet, Document, Folder
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound
from docuflow.platform.ids import uuid7

MAX_DEPTH = 32
MAX_COPY_FOLDERS = 1000
RECENT_DOCUMENTS = 5
CREATOR_PERMISSIONS = tuple(code for code in RESOURCE_PERMISSIONS if code != "dispose")
RESTRICTED = "(restricted)"


@dataclass(frozen=True)
class Destination:
    cabinet_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None

    def validate(self) -> None:
        if (self.cabinet_id is None) == (self.folder_id is None):
            raise DomainError("Choose either a cabinet or a folder.")


@dataclass(frozen=True)
class Crumb:
    id: uuid.UUID | None
    name: str
    kind: str


@dataclass(frozen=True)
class Stats:
    subfolders: int
    documents: int
    subtree_documents: int
    storage_bytes: int
    recent: list[Document] = field(default_factory=list)


@dataclass(frozen=True)
class Children:
    folders: list[Folder]
    documents: list[Document]
    total_documents: int


@dataclass(frozen=True)
class _Target:
    cabinet: Cabinet
    folder: Folder | None
    anchor_id: uuid.UUID


def clean_name(raw: str) -> str:
    name = raw.strip()
    if not name or len(name) > 200:
        raise DomainError("Names must be between 1 and 200 characters.")
    if name in {".", ".."} or any(char in name for char in "/\\") or any(ord(c) < 32 for c in name):
        raise DomainError("Names cannot contain slashes or control characters.")
    return name


class RepositoryService:
    def __init__(self, audit: AuditLog, policy: PolicyService) -> None:
        self._audit = audit
        self._policy = policy

    # Shared helpers

    @staticmethod
    def _lock_structure(session: Session, tenant_id: uuid.UUID) -> None:
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"structure:{tenant_id}"},
        )

    def _tree(self, session: Session, principal: Principal) -> AnchorTree:
        return self._policy.tree(session, principal.tenant_id, principal.user_id)

    @staticmethod
    def _require(tree: AnchorTree, anchor_id: uuid.UUID, permission: str) -> None:
        if not tree.decide(anchor_id, permission).allowed:
            raise Forbidden(
                "You do not have permission to do that.", details={"required": permission}
            )

    def _record(
        self,
        session: Session,
        principal: Principal,
        action: str,
        object_type: str,
        object_id: uuid.UUID,
        details: dict[str, Any],
    ) -> None:
        self._audit.record(
            session,
            principal.tenant_id,
            action,
            actor=AuditActor(
                principal.user_id, "user" if principal.kind == "session" else "api_key"
            ),
            object_type=object_type,
            object_id=str(object_id),
            details=details,
        )

    @staticmethod
    def _flush_unique(session: Session, message: str) -> None:
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError as exc:
            raise Conflict(message) from exc

    @staticmethod
    def _check_version(current: int, expected: int) -> None:
        if current != expected:
            raise Conflict("This item was changed by someone else. Reload and try again.")

    def _require_system(self, session: Session, principal: Principal, permission: str) -> None:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        if permission not in held:
            raise Forbidden(
                "You do not have permission to do that.", details={"required": permission}
            )

    @staticmethod
    def _new_anchor(
        session: Session,
        tenant_id: uuid.UUID,
        kind: str,
        resource_id: uuid.UUID,
        parent: uuid.UUID | None,
    ) -> AclAnchor:
        anchor = AclAnchor(
            tenant_id=tenant_id,
            resource_type=kind,
            resource_id=resource_id,
            parent_anchor_id=parent,
        )
        session.add(anchor)
        session.flush()
        return anchor

    def ensure_tenant_anchor(self, session: Session, tenant_id: uuid.UUID) -> AclAnchor:
        existing = session.scalar(
            select(AclAnchor).where(
                AclAnchor.resource_type == "tenant", AclAnchor.resource_id == tenant_id
            )
        )
        return existing or self._new_anchor(session, tenant_id, "tenant", tenant_id, None)

    @staticmethod
    def _subtree(session: Session, root_id: uuid.UUID) -> list[Folder]:
        base = select(Folder.id).where(Folder.id == root_id).cte("subtree", recursive=True)
        walk = base.union_all(select(Folder.id).join(base, Folder.parent_id == base.c.id))
        return list(
            session.scalars(
                select(Folder).where(Folder.id.in_(select(walk.c.id))).order_by(Folder.depth)
            )
        )

    # Loading with visibility

    def _cabinet(
        self, session: Session, tree: AnchorTree, cabinet_id: uuid.UUID, *, lock: bool = False
    ) -> Cabinet:
        query = select(Cabinet).where(Cabinet.id == cabinet_id)
        cabinet = session.scalar(query.with_for_update() if lock else query)
        if cabinet is None or not tree.decide(cabinet.acl_anchor_id, "read").allowed:
            raise NotFound("Cabinet not found.")
        return cabinet

    def _folder(
        self, session: Session, tree: AnchorTree, folder_id: uuid.UUID, *, lock: bool = False
    ) -> Folder:
        query = select(Folder).where(Folder.id == folder_id)
        folder = session.scalar(query.with_for_update() if lock else query)
        if folder is None or not tree.decide(folder.acl_anchor_id, "read").allowed:
            raise NotFound("Folder not found.")
        return folder

    def _document(
        self, session: Session, tree: AnchorTree, document_id: uuid.UUID, *, lock: bool = False
    ) -> Document:
        query = select(Document).where(Document.id == document_id)
        document = session.scalar(query.with_for_update() if lock else query)
        if document is None or not tree.decide(document.acl_anchor_id, "read").allowed:
            raise NotFound("Document not found.")
        return document

    def _target(self, session: Session, tree: AnchorTree, destination: Destination) -> _Target:
        destination.validate()
        if destination.folder_id:
            folder = self._folder(session, tree, destination.folder_id)
            cabinet = session.get(Cabinet, folder.cabinet_id)
            assert cabinet is not None
            return _Target(cabinet, folder, folder.acl_anchor_id)
        assert destination.cabinet_id is not None
        cabinet = self._cabinet(session, tree, destination.cabinet_id)
        return _Target(cabinet, None, cabinet.acl_anchor_id)

    @staticmethod
    def _ensure_writable(session: Session, target: _Target) -> None:
        """Archived cabinets and folders, and everything below them, accept no new content."""
        if target.cabinet.status != "active":
            raise Conflict("That cabinet is archived.")
        folder = target.folder
        while folder is not None:
            if folder.status != "active":
                raise Conflict("That folder, or one above it, is archived.")
            folder = session.get(Folder, folder.parent_id) if folder.parent_id else None

    # Cabinets

    def create_cabinet(
        self, session: Session, principal: Principal, name: str, description: str
    ) -> Cabinet:
        self._require_system(session, principal, "cabinets.manage")
        self._lock_structure(session, principal.tenant_id)
        name = clean_name(name)
        root = self.ensure_tenant_anchor(session, principal.tenant_id)
        cabinet_id = uuid7()
        anchor = self._new_anchor(session, principal.tenant_id, "cabinet", cabinet_id, root.id)
        cabinet = Cabinet(
            id=cabinet_id,
            tenant_id=principal.tenant_id,
            name=name,
            description=description,
            acl_anchor_id=anchor.id,
            created_by=principal.user_id,
        )
        session.add(cabinet)
        self._flush_unique(session, "A cabinet with that name already exists.")
        # The creator is the first owner, otherwise nobody could use the new cabinet.
        session.add_all(
            AclEntry(
                tenant_id=principal.tenant_id,
                anchor_id=anchor.id,
                principal_type="user",
                principal_id=principal.user_id,
                permission=permission,
                effect="allow",
            )
            for permission in CREATOR_PERMISSIONS
        )
        bump_permission_epoch(session, principal.tenant_id)
        self._record(session, principal, "cabinet.created", "cabinet", cabinet.id, {"name": name})
        return cabinet

    def get_cabinet(self, session: Session, principal: Principal, cabinet_id: uuid.UUID) -> Cabinet:
        return self._cabinet(session, self._tree(session, principal), cabinet_id)

    def list_cabinets(
        self,
        session: Session,
        principal: Principal,
        *,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[Cabinet], int]:
        readable = self._tree(session, principal).allowed_anchors("read")
        query = select(Cabinet).where(Cabinet.acl_anchor_id.in_(readable))
        if not include_archived:
            query = query.where(Cabinet.status == "active")
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        rows = session.scalars(query.order_by(func.lower(Cabinet.name)).limit(limit).offset(offset))
        return list(rows), int(total or 0)

    def update_cabinet(
        self,
        session: Session,
        principal: Principal,
        cabinet_id: uuid.UUID,
        *,
        row_version: int,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> Cabinet:
        self._lock_structure(session, principal.tenant_id)
        tree = self._tree(session, principal)
        cabinet = self._cabinet(session, tree, cabinet_id, lock=True)
        self._check_version(cabinet.row_version, row_version)
        before = {"name": cabinet.name, "status": cabinet.status}
        if name is not None or description is not None:
            self._require(tree, cabinet.acl_anchor_id, "edit_metadata")
        if status is not None and status != cabinet.status:
            self._require(tree, cabinet.acl_anchor_id, "delete")
            cabinet.status = status
        if name is not None:
            cabinet.name = clean_name(name)
        if description is not None:
            cabinet.description = description
        cabinet.row_version += 1
        self._flush_unique(session, "A cabinet with that name already exists.")
        self._record(
            session,
            principal,
            "cabinet.updated",
            "cabinet",
            cabinet.id,
            {"before": before, "after": {"name": cabinet.name, "status": cabinet.status}},
        )
        return cabinet

    def delete_cabinet(self, session: Session, principal: Principal, cabinet_id: uuid.UUID) -> None:
        self._lock_structure(session, principal.tenant_id)
        tree = self._tree(session, principal)
        cabinet = self._cabinet(session, tree, cabinet_id, lock=True)
        self._require(tree, cabinet.acl_anchor_id, "delete")
        in_use = session.scalar(
            select(func.count()).select_from(Folder).where(Folder.cabinet_id == cabinet.id)
        ) or session.scalar(
            select(func.count()).select_from(Document).where(Document.cabinet_id == cabinet.id)
        )
        if in_use:
            raise Conflict("Only an empty cabinet can be deleted. Archive it instead.")
        anchor = session.get(AclAnchor, cabinet.acl_anchor_id)
        name = cabinet.name
        session.delete(cabinet)
        session.flush()
        if anchor is not None:
            session.delete(anchor)
        bump_permission_epoch(session, principal.tenant_id)
        self._record(session, principal, "cabinet.deleted", "cabinet", cabinet_id, {"name": name})

    # Folders

    def create_folder(
        self, session: Session, principal: Principal, destination: Destination, name: str
    ) -> Folder:
        self._lock_structure(session, principal.tenant_id)
        tree = self._tree(session, principal)
        target = self._target(session, tree, destination)
        self._require(tree, target.anchor_id, "create")
        self._ensure_writable(session, target)
        name = clean_name(name)
        depth = 1 if target.folder is None else target.folder.depth + 1
        if depth > MAX_DEPTH:
            raise DomainError(f"Folders can be nested at most {MAX_DEPTH} levels deep.")
        folder_id = uuid7()
        anchor = self._new_anchor(
            session, principal.tenant_id, "folder", folder_id, target.anchor_id
        )
        folder = Folder(
            id=folder_id,
            tenant_id=principal.tenant_id,
            cabinet_id=target.cabinet.id,
            parent_id=target.folder.id if target.folder else None,
            name=name,
            depth=depth,
            acl_anchor_id=anchor.id,
            created_by=principal.user_id,
        )
        session.add(folder)
        self._flush_unique(session, "A folder with that name already exists here.")
        bump_permission_epoch(session, principal.tenant_id)
        self._record(
            session,
            principal,
            "folder.created",
            "folder",
            folder.id,
            {"name": name, "cabinet_id": target.cabinet.id, "parent_id": folder.parent_id},
        )
        return folder

    def get_folder(
        self, session: Session, principal: Principal, folder_id: uuid.UUID
    ) -> tuple[Folder, list[Crumb]]:
        tree = self._tree(session, principal)
        folder = self._folder(session, tree, folder_id)
        return folder, self._breadcrumb(session, tree, folder)

    def _breadcrumb(self, session: Session, tree: AnchorTree, folder: Folder) -> list[Crumb]:
        """Ancestors the caller cannot read are shown as restricted, without their names."""
        crumbs = [Crumb(folder.id, folder.name, "folder")]
        current = folder
        while current.parent_id is not None:
            parent = session.get(Folder, current.parent_id)
            assert parent is not None
            readable = tree.decide(parent.acl_anchor_id, "read").allowed
            crumbs.append(
                Crumb(parent.id, parent.name, "folder")
                if readable
                else Crumb(None, RESTRICTED, "folder")
            )
            current = parent
        cabinet = session.get(Cabinet, folder.cabinet_id)
        assert cabinet is not None
        if tree.decide(cabinet.acl_anchor_id, "read").allowed:
            crumbs.append(Crumb(cabinet.id, cabinet.name, "cabinet"))
        else:
            crumbs.append(Crumb(None, RESTRICTED, "cabinet"))
        return list(reversed(crumbs))

    def update_folder(
        self,
        session: Session,
        principal: Principal,
        folder_id: uuid.UUID,
        *,
        row_version: int,
        name: str | None = None,
        status: str | None = None,
    ) -> Folder:
        self._lock_structure(session, principal.tenant_id)
        tree = self._tree(session, principal)
        folder = self._folder(session, tree, folder_id, lock=True)
        self._check_version(folder.row_version, row_version)
        before = {"name": folder.name, "status": folder.status}
        if name is not None:
            self._require(tree, folder.acl_anchor_id, "edit_metadata")
            folder.name = clean_name(name)
        if status is not None and status != folder.status:
            self._require(tree, folder.acl_anchor_id, "delete")
            folder.status = status
        folder.row_version += 1
        self._flush_unique(session, "A folder with that name already exists here.")
        self._record(
            session,
            principal,
            "folder.updated",
            "folder",
            folder.id,
            {"before": before, "after": {"name": folder.name, "status": folder.status}},
        )
        return folder

    def move_folder(
        self,
        session: Session,
        principal: Principal,
        folder_id: uuid.UUID,
        destination: Destination,
        row_version: int,
    ) -> Folder:
        self._lock_structure(session, principal.tenant_id)
        tree = self._tree(session, principal)
        folder = self._folder(session, tree, folder_id, lock=True)
        self._check_version(folder.row_version, row_version)
        self._require(tree, folder.acl_anchor_id, "move")
        target = self._target(session, tree, destination)
        self._require(tree, target.anchor_id, "create")
        self._ensure_writable(session, target)

        subtree = self._subtree(session, folder.id)
        if target.folder is not None and target.folder.id in {item.id for item in subtree}:
            raise Conflict("A folder cannot be moved into itself or one of its own subfolders.")
        new_parent_id = target.folder.id if target.folder else None
        if new_parent_id == folder.parent_id and target.cabinet.id == folder.cabinet_id:
            return folder
        new_depth = 1 if target.folder is None else target.folder.depth + 1
        height = max(item.depth for item in subtree) - folder.depth
        if new_depth + height > MAX_DEPTH:
            raise DomainError(f"Folders can be nested at most {MAX_DEPTH} levels deep.")

        before = {"parent_id": folder.parent_id, "cabinet_id": folder.cabinet_id}
        shift = new_depth - folder.depth
        cabinet_changed = target.cabinet.id != folder.cabinet_id
        folder.parent_id = new_parent_id
        for item in subtree:
            item.depth += shift
            if cabinet_changed:
                item.cabinet_id = target.cabinet.id
        # Surface a name clash here, before the bulk update below triggers an automatic flush.
        self._flush_unique(session, "A folder with that name already exists in the destination.")
        if cabinet_changed:
            session.execute(
                update(Document)
                .where(Document.folder_id.in_([item.id for item in subtree]))
                .values(cabinet_id=target.cabinet.id)
                .execution_options(synchronize_session=False)
            )
        anchor = session.get(AclAnchor, folder.acl_anchor_id)
        assert anchor is not None
        anchor.parent_anchor_id = target.anchor_id
        folder.row_version += 1
        bump_permission_epoch(session, principal.tenant_id)
        self._record(
            session,
            principal,
            "folder.moved",
            "folder",
            folder.id,
            {
                "before": before,
                "after": {"parent_id": new_parent_id, "cabinet_id": target.cabinet.id},
                "folders_in_subtree": len(subtree),
            },
        )
        return folder

    def copy_folder(
        self,
        session: Session,
        principal: Principal,
        folder_id: uuid.UUID,
        destination: Destination,
    ) -> tuple[Folder, int, int]:
        """Copies the folder structure. Documents are not copied, because a copy needs its own
        versions and binaries, which arrive with F03. Subfolders the caller cannot read are
        skipped. Returns the new folder, the number copied, and the number skipped."""
        self._lock_structure(session, principal.tenant_id)
        tree = self._tree(session, principal)
        source = self._folder(session, tree, folder_id)
        target = self._target(session, tree, destination)
        self._require(tree, target.anchor_id, "create")
        self._ensure_writable(session, target)

        subtree = self._subtree(session, source.id)
        copied: dict[uuid.UUID, Folder] = {}
        skipped = 0
        base_depth = 1 if target.folder is None else target.folder.depth + 1
        new_parent_id = target.folder.id if target.folder else None
        name = self._free_name(session, target, new_parent_id, source.name)
        for item in subtree:
            if item.id != source.id and (
                item.parent_id not in copied or not tree.decide(item.acl_anchor_id, "read").allowed
            ):
                skipped += 1
                continue
            if len(copied) >= MAX_COPY_FOLDERS:
                raise DomainError(f"At most {MAX_COPY_FOLDERS} folders can be copied at once.")
            depth = base_depth + item.depth - source.depth
            if depth > MAX_DEPTH:
                raise DomainError(f"Folders can be nested at most {MAX_DEPTH} levels deep.")
            parent = copied.get(item.parent_id) if item.parent_id else None
            parent_anchor = (
                parent.acl_anchor_id if item.id != source.id and parent else target.anchor_id
            )
            new_id = uuid7()
            anchor = self._new_anchor(session, principal.tenant_id, "folder", new_id, parent_anchor)
            clone = Folder(
                id=new_id,
                tenant_id=principal.tenant_id,
                cabinet_id=target.cabinet.id,
                parent_id=new_parent_id if item.id == source.id else parent.id if parent else None,
                name=name if item.id == source.id else item.name,
                depth=depth,
                acl_anchor_id=anchor.id,
                created_by=principal.user_id,
            )
            session.add(clone)
            session.flush()
            copied[item.id] = clone
        bump_permission_epoch(session, principal.tenant_id)
        root = copied[source.id]
        self._record(
            session,
            principal,
            "folder.copied",
            "folder",
            root.id,
            {"source_id": source.id, "copied": len(copied), "skipped": skipped},
        )
        return root, len(copied), skipped

    @staticmethod
    def _free_name(
        session: Session, target: _Target, parent_id: uuid.UUID | None, wanted: str
    ) -> str:
        taken = {
            row.lower()
            for row in session.scalars(
                select(Folder.name).where(
                    Folder.cabinet_id == target.cabinet.id,
                    Folder.parent_id.is_(None)
                    if parent_id is None
                    else Folder.parent_id == parent_id,
                )
            )
        }
        if wanted.lower() not in taken:
            return wanted
        candidate, number = f"{wanted} (copy)", 2
        while candidate.lower() in taken:
            candidate = f"{wanted} (copy {number})"
            number += 1
        return candidate

    def delete_folder(self, session: Session, principal: Principal, folder_id: uuid.UUID) -> None:
        self._lock_structure(session, principal.tenant_id)
        tree = self._tree(session, principal)
        folder = self._folder(session, tree, folder_id, lock=True)
        self._require(tree, folder.acl_anchor_id, "delete")
        children = session.scalar(
            select(func.count()).select_from(Folder).where(Folder.parent_id == folder.id)
        )
        documents = session.scalar(
            select(func.count()).select_from(Document).where(Document.folder_id == folder.id)
        )
        if children or documents:
            raise Conflict("Only an empty folder can be deleted. Archive it instead.")
        anchor = session.get(AclAnchor, folder.acl_anchor_id)
        details = {"name": folder.name, "cabinet_id": folder.cabinet_id}
        session.delete(folder)
        session.flush()
        if anchor is not None:
            session.delete(anchor)
        bump_permission_epoch(session, principal.tenant_id)
        self._record(session, principal, "folder.deleted", "folder", folder_id, details)

    # Browsing

    def children(
        self,
        session: Session,
        principal: Principal,
        container: Destination,
        *,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> Children:
        tree = self._tree(session, principal)
        target = self._target(session, tree, container)
        readable = tree.allowed_anchors("read")
        folder_query = select(Folder).where(
            Folder.cabinet_id == target.cabinet.id,
            Folder.parent_id.is_(None)
            if target.folder is None
            else Folder.parent_id == target.folder.id,
            Folder.acl_anchor_id.in_(readable),
        )
        document_query = select(Document).where(
            Document.cabinet_id == target.cabinet.id,
            Document.folder_id.is_(None)
            if target.folder is None
            else Document.folder_id == target.folder.id,
            Document.acl_anchor_id.in_(readable),
        )
        if not include_archived:
            folder_query = folder_query.where(Folder.status == "active")
            document_query = document_query.where(Document.status == "active")
        folders = list(session.scalars(folder_query.order_by(func.lower(Folder.name))))
        total = session.scalar(select(func.count()).select_from(document_query.subquery()))
        documents = list(
            session.scalars(
                document_query.order_by(func.lower(Document.title), Document.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return Children(folders, documents, int(total or 0))

    def stats(self, session: Session, principal: Principal, container: Destination) -> Stats:
        """Counts only what the caller may read, so totals cannot reveal hidden documents."""
        tree = self._tree(session, principal)
        target = self._target(session, tree, container)
        readable = tree.allowed_anchors("read")
        if target.folder is None:
            direct_filter: tuple[Any, ...] = (
                Document.cabinet_id == target.cabinet.id,
                Document.folder_id.is_(None),
            )
            subtree_filter: tuple[Any, ...] = (Document.cabinet_id == target.cabinet.id,)
            direct_folders: Any = Folder.parent_id.is_(None) & (
                Folder.cabinet_id == target.cabinet.id
            )
        else:
            subtree_ids = [item.id for item in self._subtree(session, target.folder.id)]
            direct_filter = (Document.folder_id == target.folder.id,)
            subtree_filter = (Document.folder_id.in_(subtree_ids),)
            direct_folders = Folder.parent_id == target.folder.id
        visible = Document.acl_anchor_id.in_(readable)
        subfolders = session.scalar(
            select(func.count())
            .select_from(Folder)
            .where(direct_folders, Folder.acl_anchor_id.in_(readable))
        )
        documents = session.scalar(
            select(func.count()).select_from(Document).where(*direct_filter, visible)
        )
        subtree_documents, storage = session.execute(
            select(func.count(), func.coalesce(func.sum(Document.size_bytes), 0)).where(
                *subtree_filter, visible
            )
        ).one()
        recent = list(
            session.scalars(
                select(Document)
                .where(*subtree_filter, visible)
                .order_by(Document.created_at.desc(), Document.id)
                .limit(RECENT_DOCUMENTS)
            )
        )
        return Stats(
            int(subfolders or 0), int(documents or 0), int(subtree_documents), int(storage), recent
        )

    # Documents

    def file_document(
        self,
        session: Session,
        principal: Principal,
        destination: Destination,
        title: str,
        size_bytes: int = 0,
    ) -> Document:
        """Files a new document. F03's upload pipeline calls this once it has stored the bytes."""
        self._lock_structure(session, principal.tenant_id)
        tree = self._tree(session, principal)
        target = self._target(session, tree, destination)
        self._require(tree, target.anchor_id, "create")
        self._ensure_writable(session, target)
        title = title.strip()
        if not title or len(title) > 300:
            raise DomainError("Titles must be between 1 and 300 characters.")
        document = Document(
            tenant_id=principal.tenant_id,
            cabinet_id=target.cabinet.id,
            folder_id=target.folder.id if target.folder else None,
            title=title,
            acl_anchor_id=target.anchor_id,
            size_bytes=size_bytes,
            created_by=principal.user_id,
        )
        session.add(document)
        session.flush()
        self._record(
            session,
            principal,
            "document.filed",
            "document",
            document.id,
            {"title": title, "cabinet_id": target.cabinet.id, "folder_id": document.folder_id},
        )
        return document

    def get_document(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> Document:
        return self._document(session, self._tree(session, principal), document_id)

    def move_document(
        self,
        session: Session,
        principal: Principal,
        document_id: uuid.UUID,
        destination: Destination,
        row_version: int,
    ) -> Document:
        """Moves a document without changing its identity, history or own permissions."""
        self._lock_structure(session, principal.tenant_id)
        tree = self._tree(session, principal)
        document = self._document(session, tree, document_id, lock=True)
        self._check_version(document.row_version, row_version)
        self._require(tree, document.acl_anchor_id, "move")
        target = self._target(session, tree, destination)
        self._require(tree, target.anchor_id, "create")
        self._ensure_writable(session, target)
        before = {"cabinet_id": document.cabinet_id, "folder_id": document.folder_id}
        document.cabinet_id = target.cabinet.id
        document.folder_id = target.folder.id if target.folder else None
        own = session.scalar(
            select(AclAnchor).where(
                AclAnchor.resource_type == "document", AclAnchor.resource_id == document.id
            )
        )
        if own is not None:
            own.parent_anchor_id = target.anchor_id
        else:
            document.acl_anchor_id = target.anchor_id
        document.row_version += 1
        bump_permission_epoch(session, principal.tenant_id)
        self._record(
            session,
            principal,
            "document.moved",
            "document",
            document.id,
            {
                "before": before,
                "after": {"cabinet_id": document.cabinet_id, "folder_id": document.folder_id},
            },
        )
        return document

    def ensure_document_anchor(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> uuid.UUID:
        """Gives a document its own permissions, which are then exceptions to its folder's."""
        self._lock_structure(session, principal.tenant_id)
        tree = self._tree(session, principal)
        document = self._document(session, tree, document_id, lock=True)
        self._require(tree, document.acl_anchor_id, "manage_acl")
        own = session.scalar(
            select(AclAnchor).where(
                AclAnchor.resource_type == "document", AclAnchor.resource_id == document.id
            )
        )
        if own is not None:
            return own.id
        anchor = self._new_anchor(
            session, principal.tenant_id, "document", document.id, document.acl_anchor_id
        )
        document.acl_anchor_id = anchor.id
        bump_permission_epoch(session, principal.tenant_id)
        self._record(session, principal, "document.acl_anchor_created", "document", document.id, {})
        return anchor.id
