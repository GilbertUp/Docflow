"""Uploading and downloading document content.

Rules that hold throughout:
- Permission is checked before any bytes are read, and again inside the filing transaction.
- Bytes are stored, hashed and encrypted outside any database transaction, so a slow upload never
  holds a lock. If filing then fails, the stored object is removed again.
- The file type comes from the bytes. The extension only separates formats that share a signature.
- A file is scanned for malware before it is stored. An infected file is never stored, and a
  scanner that cannot answer refuses the upload instead of releasing the file unscanned.
- A duplicate upload creates a new document that shares the stored object. Whether the matching
  document is revealed depends on whether the caller may read it.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import BinaryIO, TypeVar

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docuflow.modules.documents.extraction import request_extraction
from docuflow.modules.documents.models import BinaryObject, DocumentVersion
from docuflow.modules.documents.scanner import Scanner
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.models import Document
from docuflow.modules.repository.service import Destination, RepositoryService
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import Database
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound
from docuflow.platform.storage import EncryptedObjects, StoredObject

SNIFF_BYTES = 8192
READ = 1024 * 1024
MAX_TITLE = 300
MAX_REASON = 500
OFFICE_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
TEXT_TYPES = {"txt": "text/plain", "csv": "text/csv"}
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


T = TypeVar("T")


class UnsupportedFileType(DomainError):
    status = 415
    code = "unsupported_type"


class FileTooLarge(DomainError):
    status = 413
    code = "too_large"


class MalwareDetected(DomainError):
    status = 422
    code = "malware_detected"


def clean_filename(raw: str) -> str:
    """The last path segment, without control characters, kept as metadata only."""
    name = CONTROL.sub("", raw.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    if not name or name in {".", ".."}:
        raise DomainError("Give the file a name.")
    return name[:MAX_TITLE]


def sniff(head: bytes, filename: str) -> str | None:
    """The media type of a file from its first bytes, or None when it is not accepted."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"PK\x03\x04") and extension in OFFICE_TYPES:
        return OFFICE_TYPES[extension]
    if extension in TEXT_TYPES and b"\x00" not in head:
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as exc:
            # A multi-byte character may be cut at the end of the sample.
            if exc.start < len(head) - 3:
                return None
        return TEXT_TYPES[extension]
    return None


@dataclass(frozen=True)
class Uploaded:
    document: Document
    version: DocumentVersion
    duplicate_of: uuid.UUID | None
    duplicate: bool


@dataclass(frozen=True)
class VersionAdded:
    document: Document
    version: DocumentVersion
    duplicate: bool


@dataclass(frozen=True)
class VersionInfo:
    version: DocumentVersion
    size_bytes: int
    mime: str


@dataclass(frozen=True)
class Download:
    tenant_id: uuid.UUID
    filename: str
    mime: str
    size_bytes: int
    object_key: str
    wrapped_data_key: bytes


class DocumentService:
    def __init__(
        self,
        audit: AuditLog,
        policy: PolicyService,
        repo: RepositoryService,
        objects: EncryptedObjects,
        scanner: Scanner,
        max_upload_bytes: int,
    ) -> None:
        self._audit = audit
        self._policy = policy
        self._repo = repo
        self._objects = objects
        self._scanner = scanner
        self.max_upload_bytes = max_upload_bytes

    def _record(
        self,
        session: Session,
        principal: Principal,
        action: str,
        document_id: uuid.UUID,
        details: dict[str, object],
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

    def check_can_upload(
        self, db: Database, principal: Principal, destination: Destination
    ) -> None:
        """Fails fast, before the caller sends a large body."""
        with db.tenant_session(principal.tenant_id) as session:
            self._repo.check_can_file(session, principal, destination)

    def upload(
        self,
        db: Database,
        principal: Principal,
        destination: Destination,
        filename: str,
        content: BinaryIO,
    ) -> Uploaded:
        """Files the bytes of a seekable stream, read from its start."""
        name = clean_filename(filename)
        self.check_can_upload(db, principal, destination)
        mime, stored = self._ingest(db, principal, name, content)
        return self._store_then(
            stored, lambda current: self._file(db, principal, destination, name, mime, current)
        )

    def check_can_add_version(
        self, db: Database, principal: Principal, document_id: uuid.UUID
    ) -> None:
        """Fails fast, before the caller sends a large body."""
        with db.tenant_session(principal.tenant_id) as session:
            self._repo.document_for_change(session, principal, document_id, "version")

    def add_version(
        self,
        db: Database,
        principal: Principal,
        document_id: uuid.UUID,
        filename: str,
        content: BinaryIO,
        reason: str = "",
    ) -> VersionAdded:
        """Adds a new version to a document. Earlier versions are kept and stay downloadable."""
        name = clean_filename(filename)
        reason = CONTROL.sub(" ", reason).strip()[:MAX_REASON]
        self.check_can_add_version(db, principal, document_id)
        mime, stored = self._ingest(db, principal, name, content)
        return self._store_then(
            stored,
            lambda current: self._append(db, principal, document_id, name, mime, reason, current),
        )

    def _ingest(
        self, db: Database, principal: Principal, name: str, content: BinaryIO
    ) -> tuple[str, StoredObject]:
        """Checks, scans and stores the bytes. Nothing is filed yet."""
        content.seek(0, 2)
        size = content.tell()
        content.seek(0)
        if size == 0:
            raise DomainError("The file is empty.")
        if size > self.max_upload_bytes:
            raise FileTooLarge(f"Files can be at most {self.max_upload_bytes // (1024 * 1024)} MB.")
        mime = sniff(content.read(SNIFF_BYTES), name)
        if mime is None:
            raise UnsupportedFileType(
                "That kind of file is not accepted. Use PDF, an image, Word, Excel, "
                "PowerPoint, text or CSV."
            )
        self._screen(db, principal, name, content)
        content.seek(0)

        def chunks() -> Iterator[bytes]:
            while part := content.read(READ):
                yield part

        tenant_id = principal.tenant_id

        def already_stored(sha256: str) -> bool:
            with db.tenant_session(tenant_id) as session:
                found = session.scalar(select(BinaryObject.id).where(BinaryObject.sha256 == sha256))
                return found is not None

        return mime, self._objects.put(tenant_id, chunks(), already_stored)

    def _store_then(self, stored: StoredObject, action: Callable[[StoredObject], T]) -> T:
        """Runs the filing step, and removes the stored object again if it fails."""
        try:
            try:
                return action(stored)
            except IntegrityError:
                # Another upload of the same bytes created the stored object first. Use theirs.
                self._discard(stored)
                stored = StoredObject(stored.sha256, stored.size, None, None, deduplicated=True)
                return action(stored)
        except BaseException:
            self._discard(stored)
            raise

    def _screen(self, db: Database, principal: Principal, name: str, content: BinaryIO) -> None:
        """Refuses an infected file, and records the attempt. Nothing is stored."""
        content.seek(0)
        verdict = self._scanner.scan(content)
        if verdict.clean:
            return
        content.seek(0)
        digest = hashlib.sha256()
        while part := content.read(READ):
            digest.update(part)
        with db.tenant_session(principal.tenant_id) as session:
            self._audit.record(
                session,
                principal.tenant_id,
                "document.upload_blocked",
                actor=AuditActor(
                    principal.user_id, "user" if principal.kind == "session" else "api_key"
                ),
                object_type="upload",
                object_id=digest.hexdigest(),
                details={
                    "filename": name,
                    "signature": verdict.signature,
                    "scanner": self._scanner.name,
                },
            )
        raise MalwareDetected(
            f"That file contains a virus ({verdict.signature}), so it was not stored."
        )

    def _discard(self, stored: StoredObject) -> None:
        if stored.key is not None:
            self._objects.delete(stored.key)

    @staticmethod
    def _binary(
        session: Session, principal: Principal, mime: str, stored: StoredObject
    ) -> tuple[BinaryObject, bool]:
        """The stored object for these bytes: the existing one, or a new row. And whether shared."""
        binary = session.scalar(
            select(BinaryObject).where(BinaryObject.sha256 == stored.sha256).with_for_update()
        )
        if binary is not None:
            session.execute(
                update(BinaryObject)
                .where(BinaryObject.id == binary.id)
                .values(ref_count=BinaryObject.ref_count + 1)
            )
            return binary, True
        if stored.key is None or stored.wrapped_data_key is None:
            raise Conflict("The file could not be saved. Please upload it again.")
        binary = BinaryObject(
            tenant_id=principal.tenant_id,
            sha256=stored.sha256,
            size_bytes=stored.size,
            mime=mime,
            object_key=stored.key,
            wrapped_data_key=stored.wrapped_data_key,
        )
        session.add(binary)
        session.flush()
        return binary, False

    def _file(
        self,
        db: Database,
        principal: Principal,
        destination: Destination,
        name: str,
        mime: str,
        stored: StoredObject,
    ) -> Uploaded:
        with db.tenant_session(principal.tenant_id) as session:
            binary, duplicate = self._binary(session, principal, mime, stored)
            duplicate_of = (
                self._readable_match(session, principal, binary.id) if duplicate else None
            )
            document = self._repo.file_document(
                session, principal, destination, name, binary.size_bytes
            )
            version = DocumentVersion(
                tenant_id=principal.tenant_id,
                document_id=document.id,
                number=1,
                binary_object_id=binary.id,
                original_filename=name,
                scan_engine=self._scanner.name,
                state="released",
                created_by=principal.user_id,
            )
            session.add(version)
            session.flush()
            document.current_version_id = version.id
            request_extraction(session, principal.tenant_id, version, binary.mime)
            self._record(
                session,
                principal,
                "document.uploaded",
                document.id,
                {
                    "sha256": binary.sha256,
                    "size_bytes": binary.size_bytes,
                    "mime": binary.mime,
                    "duplicate": duplicate,
                },
            )
            session.flush()
            return Uploaded(document, version, duplicate_of, duplicate)

    def _append(
        self,
        db: Database,
        principal: Principal,
        document_id: uuid.UUID,
        name: str,
        mime: str,
        reason: str,
        stored: StoredObject,
    ) -> VersionAdded:
        with db.tenant_session(principal.tenant_id) as session:
            # The row lock makes concurrent new versions take turns, so numbers never repeat.
            document = self._repo.document_for_change(
                session, principal, document_id, "version", lock=True
            )
            binary, duplicate = self._binary(session, principal, mime, stored)
            current = (
                session.get(DocumentVersion, document.current_version_id)
                if document.current_version_id
                else None
            )
            if current is not None and current.binary_object_id == binary.id:
                raise Conflict("That file is identical to the current version.")
            number = (
                session.scalar(
                    select(func.max(DocumentVersion.number)).where(
                        DocumentVersion.document_id == document_id
                    )
                )
                or 0
            ) + 1
            version = DocumentVersion(
                tenant_id=principal.tenant_id,
                document_id=document_id,
                number=number,
                binary_object_id=binary.id,
                original_filename=name,
                reason=reason,
                scan_engine=self._scanner.name,
                state="released",
                created_by=principal.user_id,
            )
            session.add(version)
            session.flush()
            document.current_version_id = version.id
            request_extraction(session, principal.tenant_id, version, binary.mime)
            document.size_bytes = binary.size_bytes
            document.row_version += 1
            self._record(
                session,
                principal,
                "document.version_added",
                document_id,
                {
                    "number": number,
                    "sha256": binary.sha256,
                    "size_bytes": binary.size_bytes,
                    "mime": binary.mime,
                    "reason": reason,
                    "duplicate": duplicate,
                },
            )
            session.flush()
            return VersionAdded(document, version, duplicate)

    def restore_version(
        self, db: Database, principal: Principal, document_id: uuid.UUID, number: int
    ) -> VersionAdded:
        """Makes an earlier version current again, as a new version. History is never rewritten."""
        with db.tenant_session(principal.tenant_id) as session:
            document = self._repo.document_for_change(
                session, principal, document_id, "version", lock=True
            )
            source = session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document_id, DocumentVersion.number == number
                )
            )
            if source is None:
                raise NotFound("That version does not exist.")
            if source.state != "released":
                raise Conflict("Only a released version can be restored.")
            if document.current_version_id == source.id:
                raise Conflict("That is already the current version.")
            current = (
                session.get(DocumentVersion, document.current_version_id)
                if document.current_version_id
                else None
            )
            if current is not None and current.binary_object_id == source.binary_object_id:
                raise Conflict("The current version already has that file.")
            binary = session.get(BinaryObject, source.binary_object_id)
            if binary is None:
                raise NotFound("The file of that version is no longer stored.")
            binary.ref_count += 1
            newest = (
                session.scalar(
                    select(func.max(DocumentVersion.number)).where(
                        DocumentVersion.document_id == document_id
                    )
                )
                or 0
            ) + 1
            version = DocumentVersion(
                tenant_id=principal.tenant_id,
                document_id=document_id,
                number=newest,
                binary_object_id=binary.id,
                original_filename=source.original_filename,
                reason=f"Restored from version {number}",
                scan_engine=source.scan_engine,
                state="released",
                created_by=principal.user_id,
            )
            session.add(version)
            session.flush()
            document.current_version_id = version.id
            document.size_bytes = binary.size_bytes
            document.row_version += 1
            request_extraction(session, principal.tenant_id, version, binary.mime)
            self._record(
                session,
                principal,
                "document.version_restored",
                document_id,
                {"number": newest, "restored_from": number, "sha256": binary.sha256},
            )
            session.flush()
            return VersionAdded(document, version, False)

    def _readable_match(
        self, session: Session, principal: Principal, binary_id: uuid.UUID
    ) -> uuid.UUID | None:
        """An earlier document with the same bytes, only if the caller may read it."""
        candidates = session.scalars(
            select(DocumentVersion.document_id)
            .where(DocumentVersion.binary_object_id == binary_id)
            .order_by(DocumentVersion.created_at, DocumentVersion.id)
        ).all()
        for document_id in candidates:
            try:
                self._repo.get_document(session, principal, document_id)
            except NotFound:
                continue
            return document_id
        return None

    def versions(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> list[VersionInfo]:
        self._repo.get_document(session, principal, document_id)
        rows = session.execute(
            select(DocumentVersion, BinaryObject)
            .join(BinaryObject, BinaryObject.id == DocumentVersion.binary_object_id)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.number)
        )
        return [VersionInfo(version, binary.size_bytes, binary.mime) for version, binary in rows]

    def prepare_download(
        self,
        session: Session,
        principal: Principal,
        document_id: uuid.UUID,
        number: int | None = None,
    ) -> Download:
        """The current version, or an earlier one when its number is given."""
        document = self._repo.get_document(session, principal, document_id)
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        if not tree.decide(document.acl_anchor_id, "download").allowed:
            raise Forbidden(
                "You do not have permission to do that.", details={"required": "download"}
            )
        if number is None:
            version = (
                session.get(DocumentVersion, document.current_version_id)
                if document.current_version_id
                else None
            )
        else:
            version = session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document_id, DocumentVersion.number == number
                )
            )
        if version is None:
            raise NotFound("That version does not exist.")
        if version.state != "released":
            raise Conflict("That file is not available yet.")
        binary = session.get(BinaryObject, version.binary_object_id)
        if binary is None:
            raise NotFound("That document has no file.")
        self._record(
            session,
            principal,
            "document.downloaded",
            document.id,
            {"version": version.number, "sha256": binary.sha256},
        )
        return Download(
            principal.tenant_id,
            version.original_filename,
            binary.mime,
            binary.size_bytes,
            binary.object_key,
            binary.wrapped_data_key,
        )

    def read(self, download: Download) -> Iterator[bytes]:
        return self._objects.read(
            download.tenant_id, download.object_key, download.wrapped_data_key
        )
