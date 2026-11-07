"""Importing a ZIP archive: folders from its directories and one document per file.

Rules that hold throughout:
- The importer acts as the person who submitted the archive, with that person's rights at the
  moment each file is filed. Nothing is filed that they could not have uploaded by hand, and an
  import started by someone who has since been disabled does not run.
- Every file goes through the normal upload pipeline: type from the bytes, size limit, malware
  scan, deduplication, audit. A file that fails is recorded and the rest carry on.
- The archive is untrusted. Paths that leave the target folder, links, encrypted entries,
  duplicate names, too many entries, too much expanded data and extreme compression are refused.
- Work is recorded per file, so a retry after a crash skips what is already done.
"""

from __future__ import annotations

import re
import tempfile
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import BinaryIO

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docuflow.modules.documents.models import IngestionItem, IngestionJob
from docuflow.modules.documents.service import (
    CONTROL,
    DocumentService,
    FileTooLarge,
    UnsupportedFileType,
    clean_filename,
)
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.models import User
from docuflow.modules.repository.models import Folder
from docuflow.modules.repository.service import Destination, RepositoryService
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import Database, utcnow
from docuflow.platform.errors import Conflict, DomainError, NotFound
from docuflow.platform.jobs import JobContext, enqueue
from docuflow.platform.storage import EncryptedObjects

log = structlog.get_logger("docuflow.imports")

MIB = 1024 * 1024
READ = MIB
JOB_TYPE = "documents.import"
JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
DRIVE = re.compile(r"^[A-Za-z]:")
SYMLINK = 0o120000
FILE_TYPE_MASK = 0o170000
RATIO_CHECK_FROM = MIB
ITEM_LIMIT = 500


@dataclass(frozen=True)
class ImportLimits:
    max_archive_bytes: int = 512 * MIB
    max_entries: int = 1000
    max_expanded_bytes: int = 1024 * MIB
    max_ratio: int = 200


@dataclass(frozen=True)
class Planned:
    path: str
    parts: tuple[str, ...]
    info: zipfile.ZipInfo


@dataclass(frozen=True)
class Refused:
    path: str
    reason: str


def safe_parts(name: str) -> tuple[str, ...] | None:
    """The path segments of an entry name, or None when the name could escape its folder."""
    name = name.replace("\\", "/")
    if CONTROL.search(name) or name.startswith("/") or DRIVE.match(name):
        return None
    parts = tuple(part for part in name.split("/") if part not in ("", "."))
    if not parts or ".." in parts:
        return None
    return parts


def is_junk(parts: tuple[str, ...]) -> bool:
    """Files that operating systems add and nobody means to file."""
    last = parts[-1].lower()
    return parts[0] == "__MACOSX" or last in JUNK_NAMES or last.startswith("._")


def plan_entries(
    infos: list[zipfile.ZipInfo], limits: ImportLimits
) -> tuple[list[Planned], list[Refused]]:
    """Splits an archive's entries into files to import and files to refuse, with reasons."""
    planned: list[Planned] = []
    refused: list[Refused] = []
    seen: set[str] = set()
    for info in infos:
        if info.is_dir():
            continue
        parts = safe_parts(info.filename)
        if parts is None:
            refused.append(Refused(info.filename[:1000], "The path is not safe."))
            continue
        if is_junk(parts):
            continue
        path = "/".join(parts)[:1000]
        if (info.external_attr >> 16) & FILE_TYPE_MASK == SYMLINK:
            refused.append(Refused(path, "Links are not imported."))
        elif info.flag_bits & 0x1:
            refused.append(Refused(path, "Password-protected files are not supported."))
        elif path.lower() in seen:
            refused.append(Refused(path, "The archive contains this path more than once."))
        elif info.file_size > RATIO_CHECK_FROM and info.file_size > limits.max_ratio * max(
            1, info.compress_size
        ):
            refused.append(Refused(path, "The file is compressed far more than a normal file."))
        else:
            seen.add(path.lower())
            planned.append(Planned(path, parts, info))
    return planned, refused


@dataclass(frozen=True)
class ItemView:
    path: str
    status: str
    message: str
    document_id: uuid.UUID | None
    duplicate: bool


@dataclass(frozen=True)
class ImportView:
    job: IngestionJob
    filed: int
    duplicates: int
    failed: int
    items: list[ItemView] = field(default_factory=list)


class ImportService:
    def __init__(
        self,
        audit: AuditLog,
        repo: RepositoryService,
        docs: DocumentService,
        objects: EncryptedObjects,
        limits: ImportLimits,
    ) -> None:
        self._audit = audit
        self._repo = repo
        self._docs = docs
        self._objects = objects
        self.limits = limits

    def _record(
        self,
        session: Session,
        principal: Principal,
        action: str,
        job_id: uuid.UUID,
        details: dict[str, object],
    ) -> None:
        self._audit.record(
            session,
            principal.tenant_id,
            action,
            actor=AuditActor(
                principal.user_id, "user" if principal.kind == "session" else "api_key"
            ),
            object_type="import",
            object_id=str(job_id),
            details=details,
        )

    # Submitting

    def check_can_submit(
        self, db: Database, principal: Principal, destination: Destination
    ) -> None:
        """Fails fast, before the caller sends a large archive."""
        self._docs.check_can_upload(db, principal, destination)

    def submit(
        self,
        db: Database,
        principal: Principal,
        destination: Destination,
        filename: str,
        content: BinaryIO,
    ) -> uuid.UUID:
        """Stages the archive and queues the import. Returns the import's id."""
        name = clean_filename(filename)
        self.check_can_submit(db, principal, destination)
        content.seek(0, 2)
        size = content.tell()
        content.seek(0)
        if size == 0:
            raise DomainError("The archive is empty.")
        if size > self.limits.max_archive_bytes:
            raise FileTooLarge(
                f"Archives can be at most {self.limits.max_archive_bytes // MIB} MB."
            )
        if content.read(2) != b"PK":
            raise UnsupportedFileType("Import accepts ZIP archives.")
        content.seek(0)
        try:
            with zipfile.ZipFile(content) as archive:
                infos = archive.infolist()
        except zipfile.BadZipFile as exc:
            raise DomainError("That is not a valid ZIP archive.") from exc
        if len(infos) > self.limits.max_entries:
            raise DomainError(
                f"The archive has more than {self.limits.max_entries} entries. "
                "Split it into smaller archives."
            )
        files = sum(1 for info in infos if not info.is_dir())
        content.seek(0)

        def chunks() -> Iterator[bytes]:
            while part := content.read(READ):
                yield part

        # Staged copies are not deduplicated: each belongs to one import and is deleted after.
        stored = self._objects.put(principal.tenant_id, chunks(), lambda _sha: False)
        try:
            with db.tenant_session(principal.tenant_id) as session:
                cabinet_id = destination.cabinet_id
                if destination.folder_id is not None:
                    folder = session.get(Folder, destination.folder_id)
                    assert folder is not None
                    cabinet_id = folder.cabinet_id
                assert cabinet_id is not None
                job = IngestionJob(
                    tenant_id=principal.tenant_id,
                    submitted_by=principal.user_id,
                    cabinet_id=cabinet_id,
                    folder_id=destination.folder_id,
                    archive_name=name,
                    archive_size=size,
                    archive_key=stored.key,
                    archive_wrapped_key=stored.wrapped_data_key,
                    file_count=files,
                )
                session.add(job)
                session.flush()
                enqueue(
                    session,
                    principal.tenant_id,
                    JOB_TYPE,
                    f"import:{job.id}",
                    queue="ingest",
                    payload={"ingestion_job_id": str(job.id)},
                )
                self._record(
                    session,
                    principal,
                    "import.submitted",
                    job.id,
                    {"archive": name, "size_bytes": size, "files": files},
                )
                job_id = job.id
        except BaseException:
            if stored.key is not None:
                self._objects.delete(stored.key)
            raise
        return job_id

    # Reading

    def get(
        self, session: Session, principal: Principal, job_id: uuid.UUID, items: int = ITEM_LIMIT
    ) -> ImportView:
        """An import and what happened to its files. Only the person who started it sees it."""
        job = session.scalar(
            select(IngestionJob).where(
                IngestionJob.id == job_id, IngestionJob.submitted_by == principal.user_id
            )
        )
        if job is None:
            raise NotFound("Import not found.")
        counts = {
            row[0]: row[1]
            for row in session.execute(
                select(IngestionItem.status, func.count())
                .where(IngestionItem.job_id == job_id)
                .group_by(IngestionItem.status)
            )
        }
        duplicates = (
            session.scalar(
                select(func.count()).where(
                    IngestionItem.job_id == job_id, IngestionItem.duplicate.is_(True)
                )
            )
            or 0
        )
        rows = session.scalars(
            select(IngestionItem)
            .where(IngestionItem.job_id == job_id)
            .order_by(IngestionItem.status, IngestionItem.created_at, IngestionItem.id)
            .limit(items)
        )
        views = [
            ItemView(row.path, row.status, row.message, row.document_id, row.duplicate)
            for row in rows
        ]
        return ImportView(job, counts.get("filed", 0), duplicates, counts.get("failed", 0), views)

    # Running (called by the worker)

    def run(self, db: Database, ctx: JobContext) -> None:
        job_id = uuid.UUID(ctx.payload["ingestion_job_id"])
        tenant_id = ctx.tenant_id
        with db.tenant_session(tenant_id) as session:
            job = session.scalar(
                select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()
            )
            if job is None or job.status in ("completed", "failed"):
                return
            job.status = "running"
            submitter = session.get(User, job.submitted_by)
            active = submitter is not None and submitter.status == "active"
            principal = Principal(tenant_id, job.submitted_by, "session")
            key, wrapped = job.archive_key, job.archive_wrapped_key
            where = (
                Destination(folder_id=job.folder_id)
                if job.folder_id
                else Destination(cabinet_id=job.cabinet_id)
            )
        if not active:
            self._finish(
                db,
                principal,
                job_id,
                "failed",
                "The person who started this import can no longer sign in.",
            )
            return
        if key is None or wrapped is None:
            self._finish(db, principal, job_id, "failed", "The archive is no longer available.")
            return
        with tempfile.TemporaryFile() as archive:
            for chunk in self._objects.read(tenant_id, key, wrapped):
                archive.write(chunk)
            archive.seek(0)
            try:
                with zipfile.ZipFile(archive) as zf:
                    self._process(db, ctx, principal, job_id, where, zf)
            except zipfile.BadZipFile:
                self._finish(db, principal, job_id, "failed", "The archive could not be read.")
                return
        self._finish(db, principal, job_id, "completed", None)

    def _process(
        self,
        db: Database,
        ctx: JobContext,
        principal: Principal,
        job_id: uuid.UUID,
        where: Destination,
        zf: zipfile.ZipFile,
    ) -> None:
        planned, refused = plan_entries(zf.infolist(), self.limits)
        with db.tenant_session(principal.tenant_id) as session:
            done = set(
                session.scalars(select(IngestionItem.path).where(IngestionItem.job_id == job_id))
            )
        for refusal in refused:
            if refusal.path not in done:
                self._item(db, principal, job_id, refusal.path, "failed", refusal.reason)
        folders: dict[tuple[str, ...], Destination] = {(): where}
        expanded = 0
        total = max(1, len(planned))
        for index, entry in enumerate(planned):
            ctx.report(int(100 * index / total))
            if entry.path in done:
                continue
            if expanded > self.limits.max_expanded_bytes:
                self._item(
                    db,
                    principal,
                    job_id,
                    entry.path,
                    "failed",
                    "Skipped: the archive expands to more than the allowed total.",
                )
                continue
            try:
                destination = self._destination(db, principal, folders, entry.parts[:-1])
                with self._spool(zf, entry.info) as spool:
                    expanded += spool.seek(0, 2)
                    spool.seek(0)
                    uploaded = self._docs.upload(db, principal, destination, entry.parts[-1], spool)
                self._item(
                    db,
                    principal,
                    job_id,
                    entry.path,
                    "filed",
                    "",
                    document_id=uploaded.document.id,
                    duplicate=uploaded.duplicate,
                )
            except DomainError as exc:
                self._item(db, principal, job_id, entry.path, "failed", exc.message)
            except Exception:
                # One bad file must not stop the rest. The cause is in the logs.
                log.exception("import_item_failed", job_id=str(job_id), path=entry.path)
                self._item(
                    db,
                    principal,
                    job_id,
                    entry.path,
                    "failed",
                    "That file could not be read from the archive.",
                )

    def _spool(self, zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> BinaryIO:
        """Copies one entry to a private temporary file, stopping at the size limit."""
        limit = self._docs.max_upload_bytes
        spool = tempfile.TemporaryFile()
        copied = 0
        try:
            with zf.open(info) as source:
                while part := source.read(READ):
                    copied += len(part)
                    if copied > limit:
                        raise FileTooLarge(f"Files can be at most {limit // MIB} MB.")
                    spool.write(part)
        except BaseException:
            spool.close()
            raise
        spool.seek(0)
        return spool  # type: ignore[return-value]

    def _destination(
        self,
        db: Database,
        principal: Principal,
        cache: dict[tuple[str, ...], Destination],
        parts: tuple[str, ...],
    ) -> Destination:
        """The folder for a path, made one level at a time."""
        for depth in range(1, len(parts) + 1):
            prefix = parts[:depth]
            if prefix in cache:
                continue
            parent = cache[parts[: depth - 1]]
            folder = self._ensure_folder(db, principal, parent, prefix[-1])
            cache[prefix] = Destination(folder_id=folder)
        return cache[parts]

    def _ensure_folder(
        self, db: Database, principal: Principal, parent: Destination, name: str
    ) -> uuid.UUID:
        for attempt in (1, 2):
            try:
                with db.tenant_session(principal.tenant_id) as session:
                    return self._repo.get_or_create_folder(session, principal, parent, name).id
            except Conflict:
                # Another import made it a moment ago. Looking again finds it.
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    @staticmethod
    def _item(
        db: Database,
        principal: Principal,
        job_id: uuid.UUID,
        path: str,
        status: str,
        message: str,
        *,
        document_id: uuid.UUID | None = None,
        duplicate: bool = False,
    ) -> None:
        try:
            with db.tenant_session(principal.tenant_id) as session:
                session.add(
                    IngestionItem(
                        tenant_id=principal.tenant_id,
                        job_id=job_id,
                        path=path,
                        status=status,
                        message=message[:1000],
                        document_id=document_id,
                        duplicate=duplicate,
                    )
                )
        except IntegrityError:
            # Recorded by an earlier attempt of the same job.
            return

    def _finish(
        self,
        db: Database,
        principal: Principal,
        job_id: uuid.UUID,
        status: str,
        error: str | None,
    ) -> None:
        with db.tenant_session(principal.tenant_id) as session:
            job = session.scalar(select(IngestionJob).where(IngestionJob.id == job_id))
            assert job is not None
            staged = job.archive_key
            filed = session.scalar(
                select(func.count()).where(
                    IngestionItem.job_id == job_id, IngestionItem.status == "filed"
                )
            )
            failed = session.scalar(
                select(func.count()).where(
                    IngestionItem.job_id == job_id, IngestionItem.status == "failed"
                )
            )
            session.execute(
                update(IngestionJob)
                .where(IngestionJob.id == job_id)
                .values(
                    status=status,
                    error=error,
                    finished_at=utcnow(),
                    archive_key=None,
                    archive_wrapped_key=None,
                )
            )
            self._record(
                session,
                principal,
                f"import.{status}",
                job_id,
                {"filed": filed or 0, "failed": failed or 0, "error": error},
            )
        if staged is not None:
            self._objects.delete(staged)
