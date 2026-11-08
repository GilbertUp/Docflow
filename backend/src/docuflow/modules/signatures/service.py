"""Approvals, signatures and stamps on documents (F14).

Rules that hold throughout:
- Every approval and signature is tied to one version and to the SHA-256 of the file it was made
  on. A newer version never inherits them, and they say so when read: ``current`` is true only
  while that version is still the document's current one.
- An internal signature is an attestation by a signed-in person who has just proved who they are
  by giving their password again, and who may see the document's content. It is labelled
  ``internal_attestation``. It is never presented as an external e-signature, which needs a
  provider and is a later slice.
- Wrong passwords count toward the same lockout as signing in, so a stolen session cannot be used
  to guess one.
- A signature is never changed or deleted. Its signer may revoke it with a reason, and the
  revocation is kept beside it.
- A stamp is a visible mark placed on a page of one version. The text is kept as it was applied.
  It is drawn onto the page images shown in the viewer, and never changes the stored file.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docuflow.modules.documents.models import BinaryObject, DocumentVersion
from docuflow.modules.identity import security
from docuflow.modules.identity.auth import LOCKOUT, MAX_FAILED_LOGINS, Principal
from docuflow.modules.identity.models import User
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.models import Document
from docuflow.modules.repository.service import RepositoryService
from docuflow.modules.signatures.models import Approval, Signature, Stamp, StampApplication
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import utcnow
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound

MANAGE = "workflows.manage"
MAX_STATEMENT = 500
MAX_STAMP_TEXT = 30
MAX_STAMPS_PER_PAGE = 20
COLORS = ("red", "green", "blue", "black")
DEFAULT_STATEMENT = "I have reviewed this document and approve it."


def current_version_hash(session: Session, document: Document) -> tuple[uuid.UUID, str] | None:
    """The current version and the SHA-256 of its file, or None when there is no file."""
    if document.current_version_id is None:
        return None
    version = session.get(DocumentVersion, document.current_version_id)
    binary = session.get(BinaryObject, version.binary_object_id) if version else None
    if version is None or binary is None:
        return None
    return version.id, binary.sha256


def record_approval(
    session: Session,
    tenant_id: uuid.UUID,
    document: Document,
    decider_id: uuid.UUID,
    decision: str,
    reason: str | None,
    step_id: uuid.UUID | None,
) -> Approval | None:
    """Records a workflow decision against the version and bytes it was made on."""
    found = current_version_hash(session, document)
    if found is None:
        return None
    row = Approval(
        tenant_id=tenant_id,
        document_id=document.id,
        version_id=found[0],
        version_hash=found[1],
        step_id=step_id,
        decider_id=decider_id,
        decision=decision,
        reason=reason,
    )
    session.add(row)
    session.flush()
    return row


@dataclass(frozen=True)
class ApprovalView:
    approval: Approval
    decider_name: str
    version_number: int
    current: bool


@dataclass(frozen=True)
class SignatureView:
    signature: Signature
    signer_name: str
    version_number: int
    current: bool
    intact: bool


class SignatureService:
    def __init__(self, audit: AuditLog, policy: PolicyService, repo: RepositoryService) -> None:
        self._audit = audit
        self._policy = policy
        self._repo = repo

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

    def _can(self, session: Session, principal: Principal, document: Document, right: str) -> bool:
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        return tree.decide(document.acl_anchor_id, right).allowed

    def _require(
        self, session: Session, principal: Principal, document: Document, right: str
    ) -> None:
        if not self._can(session, principal, document, right):
            raise Forbidden("You do not have permission to do that.", details={"required": right})

    def _require_manage(self, session: Session, principal: Principal) -> None:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        if MANAGE not in held:
            raise Forbidden("You do not have permission to do that.", details={"required": MANAGE})

    @staticmethod
    def _names(session: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        return {u.id: u.display_name for u in session.scalars(select(User).where(User.id.in_(ids)))}

    # Approvals

    def approvals(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> list[ApprovalView]:
        document = self._repo.get_document(session, principal, document_id)
        rows = list(
            session.scalars(
                select(Approval)
                .where(Approval.document_id == document.id)
                .order_by(Approval.created_at.desc(), Approval.id)
                .limit(200)
            )
        )
        names = self._names(session, {r.decider_id for r in rows})
        numbers = self._numbers(session, {r.version_id for r in rows})
        return [
            ApprovalView(
                r,
                names.get(r.decider_id, "Unknown"),
                numbers.get(r.version_id, 0),
                r.version_id == document.current_version_id,
            )
            for r in rows
        ]

    @staticmethod
    def _numbers(session: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not ids:
            return {}
        return {
            v.id: v.number
            for v in session.scalars(select(DocumentVersion).where(DocumentVersion.id.in_(ids)))
        }

    # Signatures

    def signatures(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> list[SignatureView]:
        document = self._repo.get_document(session, principal, document_id)
        rows = list(
            session.scalars(
                select(Signature)
                .where(Signature.document_id == document.id)
                .order_by(Signature.created_at.desc(), Signature.id)
                .limit(200)
            )
        )
        return self._views(session, document, rows)

    def _views(
        self, session: Session, document: Document, rows: list[Signature]
    ) -> list[SignatureView]:
        names = self._names(session, {r.signer_id for r in rows})
        numbers = self._numbers(session, {r.version_id for r in rows})
        hashes: dict[uuid.UUID, str] = {}
        for row in rows:
            if row.version_id not in hashes:
                version = session.get(DocumentVersion, row.version_id)
                binary = session.get(BinaryObject, version.binary_object_id) if version else None
                hashes[row.version_id] = binary.sha256 if binary else ""
        return [
            SignatureView(
                r,
                names.get(r.signer_id, "Unknown"),
                numbers.get(r.version_id, 0),
                r.version_id == document.current_version_id and r.status == "valid",
                hashes.get(r.version_id) == r.bound_hash,
            )
            for r in rows
        ]

    def sign(
        self,
        session: Session,
        principal: Principal,
        document_id: uuid.UUID,
        password: str,
        statement: str,
        approval_id: uuid.UUID | None = None,
    ) -> SignatureView | None:
        """Signs the current version. Returns None when the password is wrong.

        The caller turns None into a refusal after this session has been saved, so that the
        failed attempt still counts toward the lockout.
        """
        document = self._repo.get_document(session, principal, document_id)
        self._require(session, principal, document, "preview")
        user = session.get(User, principal.user_id)
        if user is None or principal.kind != "session":
            raise Forbidden("Only a signed-in person can sign.")
        now = utcnow()
        if user.locked_until is not None and user.locked_until > now:
            raise Forbidden("This account is locked for a while. Try again later.")
        if not security.verify_password(user.password_hash, password):
            user.failed_logins += 1
            reason = "bad_password"
            if user.failed_logins >= MAX_FAILED_LOGINS:
                user.locked_until = now + LOCKOUT
                user.failed_logins = 0
                reason = "locked"
            self._record(
                session, principal, "signature.refused", "document", document.id, {"reason": reason}
            )
            return None
        user.failed_logins = 0
        statement = statement.strip() or DEFAULT_STATEMENT
        if len(statement) > MAX_STATEMENT:
            raise DomainError(f"A statement can be up to {MAX_STATEMENT} characters.")
        found = current_version_hash(session, document)
        if found is None:
            raise NotFound("That document has no file.")
        version_id, digest = found
        already = session.scalar(
            select(Signature.id).where(
                Signature.version_id == version_id,
                Signature.signer_id == principal.user_id,
                Signature.status == "valid",
            )
        )
        if already is not None:
            raise Conflict("You have already signed this version.")
        row = Signature(
            tenant_id=principal.tenant_id,
            document_id=document.id,
            version_id=version_id,
            signer_id=principal.user_id,
            statement=statement,
            bound_hash=digest,
            approval_id=approval_id,
        )
        session.add(row)
        session.flush()
        self._record(
            session,
            principal,
            "signature.created",
            "document",
            document.id,
            {"signature": str(row.id), "version_hash": digest, "kind": row.kind},
        )
        return self._views(session, document, [row])[0]

    def revoke(
        self, session: Session, principal: Principal, signature_id: uuid.UUID, reason: str
    ) -> SignatureView:
        row = session.get(Signature, signature_id)
        document = session.get(Document, row.document_id) if row else None
        if row is None or document is None or not self._can(session, principal, document, "read"):
            raise NotFound("Signature not found.")
        if row.signer_id != principal.user_id:
            raise Forbidden("Only the person who signed can revoke it.")
        if row.status == "revoked":
            raise Conflict("That signature has already been revoked.")
        reason = reason.strip()
        if not reason or len(reason) > MAX_STATEMENT:
            raise DomainError("Say why it is being revoked, in up to 500 characters.")
        row.status = "revoked"
        row.revoked_at = utcnow()
        row.revoked_reason = reason
        self._record(
            session,
            principal,
            "signature.revoked",
            "document",
            document.id,
            {"signature": str(row.id), "reason": reason[:200]},
        )
        return self._views(session, document, [row])[0]

    # Stamp templates

    def stamps(self, session: Session, include_archived: bool = False) -> list[Stamp]:
        query = select(Stamp).order_by(Stamp.name)
        if not include_archived:
            query = query.where(Stamp.status == "active")
        return list(session.scalars(query))

    def create_stamp(
        self, session: Session, principal: Principal, name: str, text: str, color: str
    ) -> Stamp:
        self._require_manage(session, principal)
        name, text = name.strip(), text.strip()
        if not name or len(name) > 60:
            raise DomainError("Names must be between 1 and 60 characters.")
        if not text or len(text) > MAX_STAMP_TEXT:
            raise DomainError(f"The stamp text must be 1 to {MAX_STAMP_TEXT} characters.")
        if color not in COLORS:
            raise DomainError("The colour must be red, green, blue or black.")
        row = Stamp(
            tenant_id=principal.tenant_id,
            name=name,
            text=text,
            color=color,
            created_by=principal.user_id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise Conflict("A stamp with that name already exists.") from exc
        self._record(
            session, principal, "stamp.created", "stamp", row.id, {"name": name, "text": text}
        )
        return row

    def set_stamp_status(
        self, session: Session, principal: Principal, stamp_id: uuid.UUID, status: str
    ) -> Stamp:
        self._require_manage(session, principal)
        row = session.get(Stamp, stamp_id)
        if row is None:
            raise NotFound("Stamp not found.")
        if status not in ("active", "archived"):
            raise DomainError("The status must be active or archived.")
        if row.status != status:
            row.status = status
            self._record(session, principal, "stamp.updated", "stamp", row.id, {"status": status})
        return row

    # Stamps on documents

    def applied(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> list[tuple[StampApplication, str, bool]]:
        document = self._repo.get_document(session, principal, document_id)
        rows = list(
            session.scalars(
                select(StampApplication)
                .where(StampApplication.document_id == document.id)
                .order_by(StampApplication.created_at, StampApplication.id)
                .limit(500)
            )
        )
        names = self._names(session, {r.applied_by for r in rows})
        return [
            (r, names.get(r.applied_by, "Unknown"), r.version_id == document.current_version_id)
            for r in rows
        ]

    def apply_stamp(
        self,
        session: Session,
        principal: Principal,
        document_id: uuid.UUID,
        stamp_id: uuid.UUID,
        page: int,
        x: float,
        y: float,
    ) -> StampApplication:
        document = self._repo.document_for_change(
            session, principal, document_id, "annotate", lock=True
        )
        stamp = session.get(Stamp, stamp_id)
        if stamp is None or stamp.status != "active":
            raise NotFound("Stamp not found.")
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise DomainError("Place the stamp inside the page.")
        if not 1 <= page <= 500:
            raise DomainError("That page does not exist.")
        found = current_version_hash(session, document)
        if found is None:
            raise NotFound("That document has no file.")
        count = len(
            session.scalars(
                select(StampApplication.id).where(
                    StampApplication.version_id == found[0], StampApplication.page == page
                )
            ).all()
        )
        if count >= MAX_STAMPS_PER_PAGE:
            raise Conflict(f"A page can have at most {MAX_STAMPS_PER_PAGE} stamps.")
        row = StampApplication(
            tenant_id=principal.tenant_id,
            document_id=document.id,
            version_id=found[0],
            stamp_id=stamp.id,
            page=page,
            x=x,
            y=y,
            text=stamp.text,
            color=stamp.color,
            applied_by=principal.user_id,
        )
        session.add(row)
        session.flush()
        self._record(
            session,
            principal,
            "document.stamped",
            "document",
            document.id,
            {"stamp": stamp.name, "page": page, "application": str(row.id)},
        )
        return row

    def remove_stamp(
        self,
        session: Session,
        principal: Principal,
        document_id: uuid.UUID,
        application_id: uuid.UUID,
    ) -> None:
        document = self._repo.get_document(session, principal, document_id)
        row = session.get(StampApplication, application_id)
        if row is None or row.document_id != document.id:
            raise NotFound("Stamp not found.")
        if row.applied_by != principal.user_id:
            self._require(session, principal, document, "edit_metadata")
        session.delete(row)
        self._record(
            session,
            principal,
            "document.stamp_removed",
            "document",
            document.id,
            {"application": str(application_id), "text": row.text, "page": row.page},
        )
