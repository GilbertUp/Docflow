"""Making and keeping smaller copies of stored files (F17).

Rules that hold throughout:
- The original and its record are never changed. A copy is a separate stored object tied to the
  version it was made from, encrypted like any other file.
- Running is a background job on the low-priority ``compress`` queue. It is asked for by someone
  who may edit the document, and is never started by itself.
- A copy is downloadable only when it passed the quality check, and only by someone with the
  ``download`` right, and the download is audited. A copy that failed is kept with its numbers
  for study, and can never be recommended or downloaded.
- Every run records the profile and library versions it used and the hash of the file it started
  from, so the same run can be repeated and compared.
- Reports say plainly how much smaller the accepted copies are, and that no storage has been
  reclaimed, because originals are kept. They never add the two together.
"""

from __future__ import annotations

import csv
import io
import sys
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy
import pypdfium2
import structlog
from PIL import __version__ as pillow_version
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from docuflow.modules.compression import engine
from docuflow.modules.compression.models import Derivative
from docuflow.modules.documents.models import BinaryObject, DocumentVersion
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.service import RepositoryService
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import Database
from docuflow.platform.errors import Conflict, Forbidden, NotFound
from docuflow.platform.jobs import JobContext, enqueue
from docuflow.platform.storage import EncryptedObjects

log = structlog.get_logger("docuflow.compression")

JOB_TYPE = "compression.run"
REPORT_READ = "audit.read"
MAX_REPORT_ROWS = 5000
EXTENSIONS = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}
CSV_COLUMNS = [
    "derivative_id",
    "document_id",
    "version",
    "status",
    "reason",
    "mime",
    "pages",
    "source_size",
    "derivative_size",
    "reduction",
    "worst_ssim",
    "mean_ssim",
    "worst_psnr",
    "seconds",
    "profile",
    "profile_version",
    "dpi",
    "created_at",
]


def environment() -> dict[str, str]:
    """The versions of what made the copy, so a run can be repeated."""
    return {
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "pillow": pillow_version,
        "pypdfium2": pypdfium2.version.PYPDFIUM_INFO.version
        if hasattr(pypdfium2, "version")
        else "unknown",
        "numpy": numpy.__version__,
    }


@dataclass(frozen=True)
class DerivativeView:
    derivative: Derivative
    version_number: int
    recommended: bool
    current_version: bool


@dataclass(frozen=True)
class Report:
    runs: dict[str, int]
    accepted_source_bytes: int
    accepted_copy_bytes: int
    copy_saving_bytes: int
    average_reduction: float | None
    # Storage actually given back. Originals are kept, so this is zero until a policy moves them.
    storage_reclaimed_bytes: int
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class CopyDownload:
    tenant_id: uuid.UUID
    filename: str
    mime: str
    size_bytes: int
    object_key: str
    wrapped_data_key: bytes


class CompressionService:
    def __init__(
        self,
        audit: AuditLog,
        policy: PolicyService,
        repo: RepositoryService,
        objects: EncryptedObjects,
    ) -> None:
        self._audit = audit
        self._policy = policy
        self._repo = repo
        self._objects = objects

    def _record(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        actor: AuditActor,
        action: str,
        document_id: uuid.UUID,
        details: dict[str, Any],
    ) -> None:
        self._audit.record(
            session,
            tenant_id,
            action,
            actor=actor,
            object_type="document",
            object_id=str(document_id),
            details=details,
        )

    @staticmethod
    def _actor(principal: Principal) -> AuditActor:
        return AuditActor(principal.user_id, "user" if principal.kind == "session" else "api_key")

    # Asking for a copy

    def request_run(self, db: Database, principal: Principal, document_id: uuid.UUID) -> int:
        """Queues a run on the document's current version. Returns that version's number."""
        with db.tenant_session(principal.tenant_id) as session:
            document = self._repo.document_for_change(
                session, principal, document_id, "edit_metadata", lock=True
            )
            version = (
                session.get(DocumentVersion, document.current_version_id)
                if document.current_version_id
                else None
            )
            if version is None:
                raise NotFound("That document has no file.")
            if version.state != "released":
                raise Conflict("This file has not passed the security scan.")
            binary = session.get(BinaryObject, version.binary_object_id)
            if binary is None or binary.mime not in engine.SUPPORTED:
                raise Conflict("This kind of file cannot be compressed. Use a PDF or an image.")
            enqueue(
                session,
                principal.tenant_id,
                JOB_TYPE,
                f"compress:{version.id}:{uuid.uuid4().hex}",
                queue="compress",
                payload={"version_id": str(version.id), "user_id": str(principal.user_id)},
            )
            return version.number

    # The job (called by the worker)

    def run(self, db: Database, ctx: JobContext) -> None:
        version_id = uuid.UUID(ctx.payload["version_id"])
        user_id = uuid.UUID(ctx.payload["user_id"]) if ctx.payload.get("user_id") else None
        tenant_id = ctx.tenant_id
        with db.tenant_session(tenant_id) as session:
            version = session.get(DocumentVersion, version_id)
            binary = session.get(BinaryObject, version.binary_object_id) if version else None
            if version is None or binary is None or version.state != "released":
                return
            key, wrapped = binary.object_key, binary.wrapped_data_key
            mime, size, sha256 = binary.mime, binary.size_bytes, binary.sha256
        profile = engine.Profile()
        with tempfile.TemporaryDirectory(prefix="docuflow-compress-") as folder:
            path = Path(folder) / "source"
            with path.open("wb") as handle:
                for chunk in self._objects.read(tenant_id, key, wrapped):
                    handle.write(chunk)
            result = engine.compress(path, mime, size, profile)
        if result.status == "unsupported":
            return
        with db.tenant_session(tenant_id) as session:
            stored_id: uuid.UUID | None = None
            if result.data is not None and result.status in ("accepted", "rejected"):
                stored_id = self._store(session, db, tenant_id, result.data, result.mime or mime)
            row = Derivative(
                tenant_id=tenant_id,
                version_id=version_id,
                status=result.status,
                reason=result.reason,
                binary_object_id=stored_id,
                mime=result.mime,
                source_sha256=sha256,
                source_size=size,
                derivative_size=result.derivative_size,
                params={**profile.as_params(), "environment": environment()},
                metrics=result.metrics(),
                created_by=user_id,
            )
            session.add(row)
            session.flush()
            document_id = session.scalar(
                select(DocumentVersion.document_id).where(DocumentVersion.id == version_id)
            )
            if document_id is not None:
                self._record(
                    session,
                    tenant_id,
                    AuditActor(user_id, "user") if user_id else AuditActor(),
                    "document.compressed",
                    document_id,
                    {
                        "version": version.number,
                        "status": result.status,
                        "derivative": str(row.id),
                        "reduction": result.metrics()["reduction"],
                        "worst_ssim": result.metrics()["worst_ssim"],
                    },
                )

    def _store(
        self, session: Session, db: Database, tenant_id: uuid.UUID, data: bytes, mime: str
    ) -> uuid.UUID:
        def already(sha256: str) -> bool:
            found = session.scalar(select(BinaryObject.id).where(BinaryObject.sha256 == sha256))
            return found is not None

        stored = self._objects.put(tenant_id, [data], already)
        existing = session.scalar(
            select(BinaryObject).where(BinaryObject.sha256 == stored.sha256).with_for_update()
        )
        if existing is not None:
            session.execute(
                update(BinaryObject)
                .where(BinaryObject.id == existing.id)
                .values(ref_count=BinaryObject.ref_count + 1)
            )
            return existing.id
        assert stored.key is not None
        assert stored.wrapped_data_key is not None
        binary = BinaryObject(
            tenant_id=tenant_id,
            sha256=stored.sha256,
            size_bytes=stored.size,
            mime=mime,
            object_key=stored.key,
            wrapped_data_key=stored.wrapped_data_key,
        )
        session.add(binary)
        session.flush()
        return binary.id

    # Reading

    def list(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> list[DerivativeView]:
        document = self._repo.get_document(session, principal, document_id)
        rows = session.execute(
            select(Derivative, DocumentVersion.number)
            .join(DocumentVersion, DocumentVersion.id == Derivative.version_id)
            .where(DocumentVersion.document_id == document.id)
            .order_by(Derivative.created_at.desc(), Derivative.id.desc())
        ).all()
        seen: set[uuid.UUID] = set()
        views: list[DerivativeView] = []
        for derivative, number in rows:
            # The newest accepted copy of a version is the one recommended for it.
            recommended = derivative.status == "accepted" and derivative.version_id not in seen
            if derivative.status == "accepted":
                seen.add(derivative.version_id)
            views.append(
                DerivativeView(
                    derivative,
                    number,
                    recommended,
                    derivative.version_id == document.current_version_id,
                )
            )
        return views

    def prepare_download(
        self,
        session: Session,
        principal: Principal,
        document_id: uuid.UUID,
        derivative_id: uuid.UUID,
    ) -> CopyDownload:
        document = self._repo.get_document(session, principal, document_id)
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        if not tree.decide(document.acl_anchor_id, "download").allowed:
            raise Forbidden(
                "You do not have permission to do that.", details={"required": "download"}
            )
        row = session.get(Derivative, derivative_id)
        version = session.get(DocumentVersion, row.version_id) if row else None
        if row is None or version is None or version.document_id != document.id:
            raise NotFound("That copy does not exist.")
        if row.status != "accepted" or row.binary_object_id is None:
            raise Conflict("Only a copy that passed the quality check can be downloaded.")
        binary = session.get(BinaryObject, row.binary_object_id)
        if binary is None:
            raise NotFound("That copy is no longer stored.")
        stem = Path(version.original_filename).stem or "document"
        extension = EXTENSIONS.get(binary.mime, "")
        self._record(
            session,
            principal.tenant_id,
            self._actor(principal),
            "document.derivative_downloaded",
            document.id,
            {"derivative": str(row.id), "version": version.number},
        )
        return CopyDownload(
            principal.tenant_id,
            f"{stem}-compressed{extension}",
            binary.mime,
            binary.size_bytes,
            binary.object_key,
            binary.wrapped_data_key,
        )

    def read(self, copy: CopyDownload) -> Iterator[bytes]:
        return self._objects.read(copy.tenant_id, copy.object_key, copy.wrapped_data_key)

    def report(self, session: Session, principal: Principal) -> Report:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        if REPORT_READ not in held:
            raise Forbidden(
                "You do not have permission to do that.", details={"required": REPORT_READ}
            )
        counts = {
            key: int(total)
            for key, total in session.execute(
                select(Derivative.status, func.count()).group_by(Derivative.status)
            ).all()
        }
        accepted = session.execute(
            select(
                func.coalesce(func.sum(Derivative.source_size), 0),
                func.coalesce(func.sum(Derivative.derivative_size), 0),
            ).where(Derivative.status == "accepted")
        ).one()
        source_bytes, copy_bytes = int(accepted[0]), int(accepted[1])
        records = session.execute(
            select(Derivative, DocumentVersion.number, DocumentVersion.document_id)
            .join(DocumentVersion, DocumentVersion.id == Derivative.version_id)
            .order_by(Derivative.created_at.desc(), Derivative.id.desc())
            .limit(MAX_REPORT_ROWS)
        ).all()
        rows: list[dict[str, Any]] = []
        for derivative, number, document_id in records:
            m = derivative.metrics or {}
            p = derivative.params or {}
            rows.append(
                {
                    "derivative_id": str(derivative.id),
                    "document_id": str(document_id),
                    "version": number,
                    "status": derivative.status,
                    "reason": derivative.reason or "",
                    "mime": derivative.mime or "",
                    "pages": m.get("pages"),
                    "source_size": derivative.source_size,
                    "derivative_size": derivative.derivative_size,
                    "reduction": m.get("reduction"),
                    "worst_ssim": m.get("worst_ssim"),
                    "mean_ssim": m.get("mean_ssim"),
                    "worst_psnr": m.get("worst_psnr"),
                    "seconds": m.get("seconds"),
                    "profile": p.get("profile"),
                    "profile_version": p.get("profile_version"),
                    "dpi": p.get("dpi"),
                    "created_at": derivative.created_at.isoformat(),
                }
            )
        reductions = [r["reduction"] for r in rows if r["status"] == "accepted" and r["reduction"]]
        return Report(
            runs={
                status: int(counts.get(status, 0))
                for status in ("accepted", "rejected", "no_benefit")
            },
            accepted_source_bytes=source_bytes,
            accepted_copy_bytes=copy_bytes,
            copy_saving_bytes=max(0, source_bytes - copy_bytes),
            average_reduction=round(sum(reductions) / len(reductions), 4) if reductions else None,
            storage_reclaimed_bytes=0,
            rows=rows,
        )

    @staticmethod
    def report_csv(report: Report) -> Iterator[bytes]:
        from docuflow.modules.audit.service import safe_cell

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(CSV_COLUMNS)
        yield buffer.getvalue().encode("utf-8")
        for row in report.rows:
            buffer.seek(0)
            buffer.truncate()
            writer.writerow([safe_cell(row[c]) for c in CSV_COLUMNS])
            yield buffer.getvalue().encode("utf-8")
