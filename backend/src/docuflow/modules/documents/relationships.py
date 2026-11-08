"""Typed links between documents (F09).

Rules that hold throughout:
- A link is made by someone who may change the document it starts from (``edit_metadata``) and
  who may read the one it points at. It is stored once, in the direction it was made.
- Reading a document shows the links it makes and the links made to it, worded for that side.
  A link is listed only when the caller may read the document at the other end, so a link never
  reveals that a hidden document exists.
- Removing a link needs ``edit_metadata`` on either end the caller can change.
- Every change is audited on both documents.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docuflow.modules.documents.models import DocumentRelationship
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.models import Document
from docuflow.modules.repository.service import RepositoryService
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.errors import Conflict, DomainError, NotFound

# Type: (wording on the document the link starts from, wording on the document it points at,
# whether the link means the same both ways).
RELATION_TYPES: dict[str, tuple[str, str, bool]] = {
    "related": ("Related to", "Related to", True),
    "supersedes": ("Supersedes", "Superseded by", False),
    "responds_to": ("Responds to", "Answered by", False),
    "attached_to": ("Attached to", "Has attachment", False),
}
MAX_LINKS = 200


@dataclass(frozen=True)
class Link:
    id: uuid.UUID
    relation_type: str
    wording: str
    direction: str  # "from" when this document made the link, "to" when it was made to it
    other: Document
    can_remove: bool


class RelationshipService:
    def __init__(self, audit: AuditLog, policy: PolicyService, repo: RepositoryService) -> None:
        self._audit = audit
        self._policy = policy
        self._repo = repo

    def _record(
        self,
        session: Session,
        principal: Principal,
        action: str,
        document_id: uuid.UUID,
        **details: object,
    ) -> None:
        self._audit.record(
            session,
            principal.tenant_id,
            action,
            actor=AuditActor(
                principal.user_id, "user" if principal.kind == "session" else "api_key"
            ),
            object_type="document",
            object_id=str(document_id),
            details=details,
        )

    def list(self, session: Session, principal: Principal, document_id: uuid.UUID) -> list[Link]:
        document = self._repo.get_document(session, principal, document_id)
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        rows = session.scalars(
            select(DocumentRelationship)
            .where(
                or_(
                    DocumentRelationship.from_document_id == document.id,
                    DocumentRelationship.to_document_id == document.id,
                )
            )
            .order_by(DocumentRelationship.created_at, DocumentRelationship.id)
            .limit(MAX_LINKS)
        ).all()
        can_change_here = tree.decide(document.acl_anchor_id, "edit_metadata").allowed
        links: list[Link] = []
        for row in rows:
            outgoing = row.from_document_id == document.id
            other = session.get(Document, row.to_document_id if outgoing else row.from_document_id)
            if other is None or not tree.decide(other.acl_anchor_id, "read").allowed:
                continue
            from_text, to_text, _same = RELATION_TYPES[row.relation_type]
            links.append(
                Link(
                    row.id,
                    row.relation_type,
                    from_text if outgoing else to_text,
                    "from" if outgoing else "to",
                    other,
                    can_change_here or tree.decide(other.acl_anchor_id, "edit_metadata").allowed,
                )
            )
        return links

    def add(
        self,
        session: Session,
        principal: Principal,
        document_id: uuid.UUID,
        to_document_id: uuid.UUID,
        relation_type: str,
    ) -> Link:
        if relation_type not in RELATION_TYPES:
            raise DomainError(
                "The link type must be one of: " + ", ".join(sorted(RELATION_TYPES)) + "."
            )
        if document_id == to_document_id:
            raise DomainError("A document cannot be linked to itself.")
        source = self._repo.document_for_change(
            session, principal, document_id, "edit_metadata", lock=True
        )
        # Unreadable is not found, so a link cannot be used to probe for hidden documents.
        target = self._repo.get_document(session, principal, to_document_id)
        _from, _to, same = RELATION_TYPES[relation_type]
        existing = [(source.id, target.id)] + ([(target.id, source.id)] if same else [])
        for first, second in existing:
            found = session.scalar(
                select(DocumentRelationship.id).where(
                    DocumentRelationship.from_document_id == first,
                    DocumentRelationship.to_document_id == second,
                    DocumentRelationship.relation_type == relation_type,
                )
            )
            if found is not None:
                raise Conflict("Those documents are already linked in that way.")
        count = len(
            session.scalars(
                select(DocumentRelationship.id).where(
                    DocumentRelationship.from_document_id == source.id
                )
            ).all()
        )
        if count >= MAX_LINKS:
            raise DomainError(f"A document can start at most {MAX_LINKS} links.")
        row = DocumentRelationship(
            tenant_id=principal.tenant_id,
            from_document_id=source.id,
            to_document_id=target.id,
            relation_type=relation_type,
            created_by=principal.user_id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise Conflict("Those documents are already linked in that way.") from exc
        for subject, other in ((source, target), (target, source)):
            self._record(
                session,
                principal,
                "document.link_added",
                subject.id,
                type=relation_type,
                other=str(other.id),
                direction="from" if subject is source else "to",
            )
        from_text, _to_text, _same = RELATION_TYPES[relation_type]
        return Link(row.id, relation_type, from_text, "from", target, True)

    def remove(
        self, session: Session, principal: Principal, document_id: uuid.UUID, link_id: uuid.UUID
    ) -> None:
        document = self._repo.get_document(session, principal, document_id)
        row = session.get(DocumentRelationship, link_id)
        if row is None or document.id not in (row.from_document_id, row.to_document_id):
            raise NotFound("Link not found.")
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        ends = [
            session.get(Document, row.from_document_id),
            session.get(Document, row.to_document_id),
        ]
        changeable = [
            end
            for end in ends
            if end is not None
            and end.status == "active"
            and tree.decide(end.acl_anchor_id, "read").allowed
            and tree.decide(end.acl_anchor_id, "edit_metadata").allowed
        ]
        if not changeable:
            # Readable but not changeable is a refusal, not a secret.
            self._repo.document_for_change(session, principal, document.id, "edit_metadata")
            raise NotFound("Link not found.")
        session.delete(row)
        for end in ends:
            if end is not None:
                self._record(
                    session,
                    principal,
                    "document.link_removed",
                    end.id,
                    type=row.relation_type,
                    from_document=str(row.from_document_id),
                    to_document=str(row.to_document_id),
                )
