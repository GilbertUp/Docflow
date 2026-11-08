"""Keeping documents for as long as they must be, and destroying them properly (F15).

Rules that hold throughout:
- A document is destroyed only through disposal, and disposal is refused unless every one of these
  is true: the document has a retention class, its retention period has ended, no active legal
  hold covers it, and the person approving holds the ``dispose`` right on it.
- A legal hold always wins. While any active hold covers a document, nothing can dispose of it,
  however old it is.
- Disposal destroys the document, its versions, its text, fields, comments, tasks, signatures and
  stamps, and the stored files that nothing else uses. Files shared with another document stay.
- Disposal leaves one permanent record that points at nothing that was deleted: the title, the
  class, who approved it, why, and the SHA-256 of every file that was destroyed.
- Setting retention and holds needs the ``retention.manage`` right and is audited, including what
  it replaced.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docuflow.modules.collaboration.models import Task
from docuflow.modules.documents.models import (
    BinaryObject,
    DocumentVersion,
    OcrResult,
    Page,
)
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.models import AclAnchor, AclEntry
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.indexing.models import (
    ClassificationResult,
    ExtractedField,
    ExtractionFeedback,
    IndexValue,
)
from docuflow.modules.repository.models import Document
from docuflow.modules.repository.service import RepositoryService
from docuflow.modules.retention.models import (
    DisposalEvent,
    DocumentRetention,
    LegalHold,
    LegalHoldItem,
    RetentionClass,
)
from docuflow.modules.signatures.models import Approval, Signature, StampApplication
from docuflow.modules.workflow.models import WorkflowEvent, WorkflowInstance, WorkflowStep
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound
from docuflow.platform.storage import EncryptedObjects

log = structlog.get_logger("docuflow.retention")

MANAGE = "retention.manage"
MAX_DAYS = 365 * 100
MAX_NAME = 120
MAX_REASON = 1000
TRIGGERS = ("created", "last_version")
MAX_CANDIDATES = 500


@dataclass(frozen=True)
class RetentionView:
    document: Document
    retention_class: RetentionClass | None
    eligible_at: datetime | None
    # not_assigned, retained, eligible or held
    status: str
    holds: list[LegalHold]


@dataclass(frozen=True)
class Candidate:
    document: Document
    class_name: str
    eligible_at: datetime


@dataclass(frozen=True)
class HoldView:
    hold: LegalHold
    documents: int


@dataclass(frozen=True)
class Disposed:
    event: DisposalEvent
    # Stored files to remove once the records are gone.
    object_keys: list[str]


class RetentionService:
    def __init__(
        self,
        audit: AuditLog,
        policy: PolicyService,
        repo: RepositoryService,
        objects: EncryptedObjects | None = None,
    ) -> None:
        self._audit = audit
        self._policy = policy
        self._repo = repo
        self._objects = objects

    # Helpers

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

    def _require_manage(self, session: Session, principal: Principal) -> None:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        if MANAGE not in held:
            raise Forbidden("You do not have permission to do that.", details={"required": MANAGE})

    def _can(self, session: Session, principal: Principal, document: Document, right: str) -> bool:
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        return tree.decide(document.acl_anchor_id, right).allowed

    @staticmethod
    def eligible_at(session: Session, document: Document, cls: RetentionClass) -> datetime:
        """When the document may first be disposed of."""
        start = document.created_at
        if cls.trigger == "last_version":
            newest = session.scalar(
                select(func.max(DocumentVersion.created_at)).where(
                    DocumentVersion.document_id == document.id
                )
            )
            start = newest or start
        return start + timedelta(days=cls.duration_days)

    @staticmethod
    def _active_holds(session: Session, document_id: uuid.UUID) -> list[LegalHold]:
        return list(
            session.scalars(
                select(LegalHold)
                .join(LegalHoldItem, LegalHoldItem.hold_id == LegalHold.id)
                .where(LegalHoldItem.document_id == document_id, LegalHold.status == "active")
                .order_by(LegalHold.name)
            )
        )

    # Classes

    def classes(self, session: Session, include_archived: bool = False) -> list[RetentionClass]:
        query = select(RetentionClass).order_by(RetentionClass.name)
        if not include_archived:
            query = query.where(RetentionClass.status == "active")
        return list(session.scalars(query))

    def create_class(
        self,
        session: Session,
        principal: Principal,
        name: str,
        description: str,
        duration_days: int,
        trigger: str,
    ) -> RetentionClass:
        self._require_manage(session, principal)
        name = name.strip()
        if not name or len(name) > MAX_NAME:
            raise DomainError(f"Names must be between 1 and {MAX_NAME} characters.")
        if not 1 <= duration_days <= MAX_DAYS:
            raise DomainError(f"Keep for 1 to {MAX_DAYS} days.")
        if trigger not in TRIGGERS:
            raise DomainError("The clock starts when the document is created or last changed.")
        row = RetentionClass(
            tenant_id=principal.tenant_id,
            name=name,
            description=description.strip()[:1000],
            duration_days=duration_days,
            trigger=trigger,
            created_by=principal.user_id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise Conflict("A retention class with that name already exists.") from exc
        self._record(
            session,
            principal,
            "retention_class.created",
            "retention_class",
            row.id,
            {"name": name, "days": duration_days, "trigger": trigger},
        )
        return row

    def update_class(
        self,
        session: Session,
        principal: Principal,
        class_id: uuid.UUID,
        *,
        name: str | None = None,
        duration_days: int | None = None,
        status: str | None = None,
    ) -> RetentionClass:
        self._require_manage(session, principal)
        row = session.get(RetentionClass, class_id)
        if row is None:
            raise NotFound("Retention class not found.")
        changes: dict[str, Any] = {}
        if name is not None and name.strip() != row.name:
            clean = name.strip()
            if not clean or len(clean) > MAX_NAME:
                raise DomainError(f"Names must be between 1 and {MAX_NAME} characters.")
            changes["name"] = [row.name, clean]
            row.name = clean
        if duration_days is not None and duration_days != row.duration_days:
            if not 1 <= duration_days <= MAX_DAYS:
                raise DomainError(f"Keep for 1 to {MAX_DAYS} days.")
            changes["days"] = [row.duration_days, duration_days]
            row.duration_days = duration_days
        if status is not None and status != row.status:
            if status not in ("active", "archived"):
                raise DomainError("The status must be active or archived.")
            changes["status"] = [row.status, status]
            row.status = status
        if changes:
            try:
                session.flush()
            except IntegrityError as exc:
                raise Conflict("A retention class with that name already exists.") from exc
            self._record(
                session,
                principal,
                "retention_class.updated",
                "retention_class",
                row.id,
                {"changes": changes},
            )
        return row

    # Documents

    def retention_of(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> RetentionView:
        document = self._repo.get_document(session, principal, document_id)
        link = session.scalar(
            select(DocumentRetention).where(DocumentRetention.document_id == document.id)
        )
        cls = session.get(RetentionClass, link.class_id) if link else None
        holds = self._active_holds(session, document.id)
        if cls is None:
            return RetentionView(document, None, None, "held" if holds else "not_assigned", holds)
        due = self.eligible_at(session, document, cls)
        if holds:
            status = "held"
        elif due <= datetime.now(due.tzinfo):
            status = "eligible"
        else:
            status = "retained"
        return RetentionView(document, cls, due, status, holds)

    def set_document_class(
        self,
        session: Session,
        principal: Principal,
        document_id: uuid.UUID,
        class_id: uuid.UUID | None,
    ) -> RetentionView:
        self._require_manage(session, principal)
        document = self._repo.get_document(session, principal, document_id)
        link = session.scalar(
            select(DocumentRetention).where(DocumentRetention.document_id == document.id)
        )
        before = session.get(RetentionClass, link.class_id) if link else None
        if class_id is None:
            if link is not None:
                session.delete(link)
        else:
            cls = session.get(RetentionClass, class_id)
            if cls is None or (cls.status != "active" and (before is None or before.id != cls.id)):
                raise NotFound("Retention class not found.")
            if link is None:
                session.add(
                    DocumentRetention(
                        tenant_id=principal.tenant_id,
                        document_id=document.id,
                        class_id=cls.id,
                        assigned_by=principal.user_id,
                    )
                )
            else:
                link.class_id = cls.id
                link.assigned_by = principal.user_id
        session.flush()
        self._record(
            session,
            principal,
            "document.retention_set",
            "document",
            document.id,
            {
                "before": before.name if before else None,
                "after": str(class_id) if class_id else None,
            },
        )
        return self.retention_of(session, principal, document.id)

    # Legal holds

    def holds(
        self, session: Session, principal: Principal, include_released: bool = False
    ) -> list[HoldView]:
        self._require_manage(session, principal)
        query = select(LegalHold).order_by(LegalHold.created_at.desc())
        if not include_released:
            query = query.where(LegalHold.status == "active")
        rows = list(session.scalars(query))
        counts = dict(
            session.execute(
                select(LegalHoldItem.hold_id, func.count()).group_by(LegalHoldItem.hold_id)
            ).tuples()
        )
        return [HoldView(row, int(counts.get(row.id, 0))) for row in rows]

    def create_hold(
        self, session: Session, principal: Principal, name: str, reason: str
    ) -> HoldView:
        self._require_manage(session, principal)
        name, reason = name.strip(), reason.strip()
        if not name or len(name) > MAX_NAME:
            raise DomainError(f"Names must be between 1 and {MAX_NAME} characters.")
        if not reason or len(reason) > MAX_REASON:
            raise DomainError("Say why the hold is needed, in up to 1000 characters.")
        row = LegalHold(
            tenant_id=principal.tenant_id, name=name, reason=reason, created_by=principal.user_id
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise Conflict("A legal hold with that name already exists.") from exc
        self._record(
            session,
            principal,
            "legal_hold.created",
            "legal_hold",
            row.id,
            {"name": name, "reason": reason[:200]},
        )
        return HoldView(row, 0)

    def _hold(self, session: Session, hold_id: uuid.UUID) -> LegalHold:
        row = session.get(LegalHold, hold_id)
        if row is None:
            raise NotFound("Legal hold not found.")
        return row

    def release_hold(
        self, session: Session, principal: Principal, hold_id: uuid.UUID, reason: str
    ) -> HoldView:
        self._require_manage(session, principal)
        row = self._hold(session, hold_id)
        if row.status != "active":
            raise Conflict("That hold has already been released.")
        reason = reason.strip()
        if not reason or len(reason) > MAX_REASON:
            raise DomainError("Say why the hold is being released.")
        row.status = "released"
        row.released_by = principal.user_id
        row.released_at = datetime.now().astimezone()
        row.release_reason = reason
        count = int(
            session.scalar(
                select(func.count())
                .select_from(LegalHoldItem)
                .where(LegalHoldItem.hold_id == row.id)
            )
            or 0
        )
        self._record(
            session,
            principal,
            "legal_hold.released",
            "legal_hold",
            row.id,
            {"reason": reason[:200], "documents": count},
        )
        return HoldView(row, count)

    def add_to_hold(
        self, session: Session, principal: Principal, hold_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        self._require_manage(session, principal)
        hold = self._hold(session, hold_id)
        if hold.status != "active":
            raise Conflict("That hold has been released.")
        document = self._repo.get_document(session, principal, document_id)
        session.add(
            LegalHoldItem(
                tenant_id=principal.tenant_id,
                hold_id=hold.id,
                document_id=document.id,
                added_by=principal.user_id,
            )
        )
        try:
            session.flush()
        except IntegrityError as exc:
            raise Conflict("That document is already on this hold.") from exc
        self._record(
            session,
            principal,
            "document.hold_placed",
            "document",
            document.id,
            {"hold": hold.name, "hold_id": str(hold.id)},
        )

    def remove_from_hold(
        self, session: Session, principal: Principal, hold_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        self._require_manage(session, principal)
        hold = self._hold(session, hold_id)
        item = session.scalar(
            select(LegalHoldItem).where(
                LegalHoldItem.hold_id == hold.id, LegalHoldItem.document_id == document_id
            )
        )
        if item is None:
            raise NotFound("That document is not on this hold.")
        session.delete(item)
        self._record(
            session,
            principal,
            "document.hold_removed",
            "document",
            document_id,
            {"hold": hold.name, "hold_id": str(hold.id)},
        )

    def hold_documents(
        self, session: Session, principal: Principal, hold_id: uuid.UUID
    ) -> tuple[list[Document], int]:
        """The documents on a hold that the caller may read, and how many there are in all."""
        self._require_manage(session, principal)
        hold = self._hold(session, hold_id)
        rows = list(
            session.scalars(
                select(Document)
                .join(LegalHoldItem, LegalHoldItem.document_id == Document.id)
                .where(LegalHoldItem.hold_id == hold.id)
                .order_by(Document.title)
                .limit(MAX_CANDIDATES)
            )
        )
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        readable = [d for d in rows if tree.decide(d.acl_anchor_id, "read").allowed]
        total = int(
            session.scalar(
                select(func.count())
                .select_from(LegalHoldItem)
                .where(LegalHoldItem.hold_id == hold.id)
            )
            or 0
        )
        return readable, total

    # Disposal

    def candidates(self, session: Session, principal: Principal) -> list[Candidate]:
        """Documents past their retention, not on hold, that the caller may dispose of."""
        now = datetime.now().astimezone()
        rows = session.execute(
            select(Document, RetentionClass)
            .join(DocumentRetention, DocumentRetention.document_id == Document.id)
            .join(RetentionClass, RetentionClass.id == DocumentRetention.class_id)
            .order_by(Document.created_at)
            .limit(MAX_CANDIDATES * 4)
        ).all()
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        held = set(
            session.scalars(
                select(LegalHoldItem.document_id)
                .join(LegalHold, LegalHold.id == LegalHoldItem.hold_id)
                .where(LegalHold.status == "active")
            )
        )
        found: list[Candidate] = []
        for document, cls in rows:
            if document.id in held:
                continue
            if not (
                tree.decide(document.acl_anchor_id, "read").allowed
                and tree.decide(document.acl_anchor_id, "dispose").allowed
            ):
                continue
            due = self.eligible_at(session, document, cls)
            if due <= now:
                found.append(Candidate(document, cls.name, due))
            if len(found) >= MAX_CANDIDATES:
                break
        return found

    def dispose(
        self, session: Session, principal: Principal, document_id: uuid.UUID, reason: str
    ) -> Disposed:
        """Destroys a document that is due. Returns the stored files to remove after saving."""
        document = self._repo.get_document(session, principal, document_id)
        if not self._can(session, principal, document, "dispose"):
            raise Forbidden(
                "You do not have permission to do that.", details={"required": "dispose"}
            )
        locked = session.scalar(
            select(Document).where(Document.id == document.id).with_for_update()
        )
        assert locked is not None
        document = locked
        link = session.scalar(
            select(DocumentRetention).where(DocumentRetention.document_id == document.id)
        )
        cls = session.get(RetentionClass, link.class_id) if link else None
        if cls is None:
            raise Conflict("This document has no retention class, so it cannot be disposed of.")
        holds = self._active_holds(session, document.id)
        if holds:
            raise Conflict(
                "This document is on legal hold: " + ", ".join(h.name for h in holds) + "."
            )
        due = self.eligible_at(session, document, cls)
        if due > datetime.now(due.tzinfo):
            raise Conflict(f"This document must be kept until {due.date().isoformat()}.")
        reason = reason.strip()
        if len(reason) > MAX_REASON:
            raise DomainError(f"A reason can be up to {MAX_REASON} characters.")

        versions = list(
            session.execute(
                select(DocumentVersion, BinaryObject)
                .join(BinaryObject, BinaryObject.id == DocumentVersion.binary_object_id)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.number)
            )
        )
        hashes = [
            {"version": v.number, "sha256": b.sha256, "size_bytes": b.size_bytes}
            for v, b in versions
        ]
        event = DisposalEvent(
            tenant_id=principal.tenant_id,
            document_id=document.id,
            title=document.title,
            retention_class=cls.name,
            eligible_at=due,
            version_hashes=hashes,
            approver_id=principal.user_id,
            reason=reason or None,
        )
        session.add(event)
        session.flush()
        self._record(
            session,
            principal,
            "document.disposed",
            "document",
            document.id,
            {
                "title": document.title,
                "class": cls.name,
                "versions": len(hashes),
                "reason": reason[:200],
                "event": str(event.id),
            },
        )
        keys = self._destroy(session, document, [v.id for v, _b in versions])
        return Disposed(event, keys)

    @staticmethod
    def _destroy(session: Session, document: Document, version_ids: list[uuid.UUID]) -> list[str]:
        """Deletes the document and everything about it, children first. Returns files to remove."""
        doc_id = document.id
        binary_ids = set(
            session.scalars(
                select(DocumentVersion.binary_object_id).where(
                    DocumentVersion.document_id == doc_id
                )
            )
        )
        field_ids = select(ExtractedField.id).where(ExtractedField.version_id.in_(version_ids))
        page_ids = select(Page.id).where(Page.version_id.in_(version_ids))
        session.execute(
            delete(ExtractionFeedback).where(ExtractionFeedback.extracted_field_id.in_(field_ids))
        )
        session.execute(delete(ExtractedField).where(ExtractedField.version_id.in_(version_ids)))
        session.execute(
            delete(ClassificationResult).where(ClassificationResult.version_id.in_(version_ids))
        )
        session.execute(delete(OcrResult).where(OcrResult.page_id.in_(page_ids)))
        session.execute(delete(Page).where(Page.version_id.in_(version_ids)))
        session.execute(delete(StampApplication).where(StampApplication.document_id == doc_id))
        session.execute(delete(Signature).where(Signature.document_id == doc_id))
        session.execute(delete(Approval).where(Approval.document_id == doc_id))
        session.execute(delete(IndexValue).where(IndexValue.document_id == doc_id))
        # A workflow step points at its task, so the steps go before the tasks.
        instances = select(WorkflowInstance.id).where(WorkflowInstance.document_id == doc_id)
        session.execute(delete(WorkflowEvent).where(WorkflowEvent.instance_id.in_(instances)))
        session.execute(delete(WorkflowStep).where(WorkflowStep.instance_id.in_(instances)))
        session.execute(delete(WorkflowInstance).where(WorkflowInstance.document_id == doc_id))
        session.execute(delete(Task).where(Task.document_id == doc_id))
        session.execute(
            update(Document).where(Document.id == doc_id).values(current_version_id=None)
        )
        session.execute(delete(DocumentVersion).where(DocumentVersion.document_id == doc_id))
        own_anchor = session.scalar(
            select(AclAnchor.id).where(
                AclAnchor.resource_type == "document", AclAnchor.resource_id == doc_id
            )
        )
        anchor_id = document.acl_anchor_id
        session.flush()
        session.expire_all()
        session.execute(delete(Document).where(Document.id == doc_id))
        if own_anchor is not None and own_anchor == anchor_id:
            session.execute(delete(AclEntry).where(AclEntry.anchor_id == own_anchor))
            session.execute(delete(AclAnchor).where(AclAnchor.id == own_anchor))
        keys: list[str] = []
        for binary_id in binary_ids:
            still_used = session.scalar(
                select(func.count())
                .select_from(DocumentVersion)
                .where(DocumentVersion.binary_object_id == binary_id)
            )
            if not still_used:
                binary = session.get(BinaryObject, binary_id)
                if binary is not None:
                    keys.append(binary.object_key)
                    session.delete(binary)
        return keys

    def purge(self, keys: list[str]) -> None:
        """Removes stored files once the records that used them are gone."""
        if self._objects is None:
            return
        for key in keys:
            try:
                self._objects.delete(key)
            except Exception:
                # The records are already gone, so this is an orphan to clean up, never a reason
                # to fail the disposal that was approved.
                log.exception("disposal_object_not_removed", key=key)

    def events(
        self, session: Session, principal: Principal, limit: int = 50
    ) -> list[DisposalEvent]:
        self._require_manage(session, principal)
        return list(
            session.scalars(
                select(DisposalEvent)
                .order_by(DisposalEvent.created_at.desc(), DisposalEvent.id)
                .limit(min(limit, 200))
            )
        )
