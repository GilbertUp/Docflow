"""Showing a document in the browser as page images (F05).

Rules that hold throughout:
- The browser never receives the file. Each page is rendered on the server into an image, so
  seeing a document needs only the ``preview`` right, not ``download``.
- Only a released version is shown, and only its current version.
- Page images are audited when viewed at reading size. Thumbnails are not, to keep the log useful.
- Nothing is stored. Rendering is cheap enough to do on request, and it keeps no unprotected copy.
"""

from __future__ import annotations

import io
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from docuflow.modules.documents.extraction import IMAGE_TYPES, MAX_SIDE, TEXT_TYPES
from docuflow.modules.documents.models import BinaryObject, DocumentVersion, Page
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.models import Document
from docuflow.modules.repository.service import RepositoryService
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import Database
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound
from docuflow.platform.storage import EncryptedObjects

MIN_WIDTH = 64
MAX_WIDTH = 2000
THUMBNAIL_WIDTH = 240
MAX_PAGES = 500


class PreviewUnavailable(DomainError):
    status = 415
    code = "preview_unavailable"


@dataclass(frozen=True)
class PreviewPage:
    number: int
    width: int
    height: int


@dataclass(frozen=True)
class PreviewInfo:
    version_number: int
    # "pages" shows page images, "text" shows the text, "none" has nothing to show.
    kind: str
    mime: str
    pages: list[PreviewPage]


@dataclass(frozen=True)
class Rendered:
    data: bytes
    media_type: str


def kind_of(mime: str) -> str:
    if mime == "application/pdf" or mime in IMAGE_TYPES:
        return "pages"
    return "text" if mime in TEXT_TYPES else "none"


class PreviewService:
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

    def _current(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> tuple[Document, DocumentVersion, BinaryObject]:
        document = self._repo.get_document(session, principal, document_id)
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        if not tree.decide(document.acl_anchor_id, "preview").allowed:
            raise Forbidden(
                "You do not have permission to do that.", details={"required": "preview"}
            )
        version = (
            session.get(DocumentVersion, document.current_version_id)
            if document.current_version_id
            else None
        )
        if version is None:
            raise NotFound("That document has no file.")
        if version.state != "released":
            raise Conflict("This file has not passed the security scan, so it cannot be shown.")
        binary = session.get(BinaryObject, version.binary_object_id)
        if binary is None:
            raise NotFound("That document has no file.")
        return document, version, binary

    def info(self, session: Session, principal: Principal, document_id: uuid.UUID) -> PreviewInfo:
        _document, version, binary = self._current(session, principal, document_id)
        kind = kind_of(binary.mime)
        pages: list[PreviewPage] = []
        if kind == "pages":
            known = session.scalars(
                select(Page).where(Page.version_id == version.id).order_by(Page.number)
            )
            pages = [PreviewPage(page.number, page.width, page.height) for page in known]
            if not pages:
                # The text has not been read yet, so count the pages from the file itself.
                count = self._count(principal, binary)
                pages = [PreviewPage(number, 0, 0) for number in range(1, count + 1)]
        return PreviewInfo(version.number, kind, binary.mime, pages)

    def _fetch(self, principal: Principal, binary: BinaryObject, path: Path) -> None:
        with path.open("wb") as handle:
            for chunk in self._objects.read(
                principal.tenant_id, binary.object_key, binary.wrapped_data_key
            ):
                handle.write(chunk)

    def _count(self, principal: Principal, binary: BinaryObject) -> int:
        with tempfile.TemporaryDirectory(prefix="docuflow-preview-") as folder:
            path = Path(folder) / "source"
            self._fetch(principal, binary, path)
            try:
                if binary.mime == "application/pdf":
                    pdf = pdfium.PdfDocument(str(path))
                    try:
                        return min(len(pdf), MAX_PAGES)
                    finally:
                        pdf.close()
                with Image.open(path) as image:
                    return min(getattr(image, "n_frames", 1), MAX_PAGES)
            except (pdfium.PdfiumError, UnidentifiedImageError, OSError) as exc:
                raise PreviewUnavailable("This file is damaged and cannot be shown.") from exc

    def render(
        self,
        db: Database,
        principal: Principal,
        document_id: uuid.UUID,
        number: int,
        width: int,
    ) -> Rendered:
        """One page as a PNG image about ``width`` pixels wide."""
        width = max(MIN_WIDTH, min(width, MAX_WIDTH))
        with db.tenant_session(principal.tenant_id) as session:
            _document, version, binary = self._current(session, principal, document_id)
            if kind_of(binary.mime) != "pages":
                raise PreviewUnavailable("This kind of file cannot be shown as pages.")
            version_number = version.number
        with tempfile.TemporaryDirectory(prefix="docuflow-preview-") as folder:
            path = Path(folder) / "source"
            self._fetch(principal, binary, path)
            image = (
                self._pdf_page(path, number, width)
                if binary.mime == "application/pdf"
                else self._image_frame(path, number, width)
            )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=False)
        if width > THUMBNAIL_WIDTH:
            with db.tenant_session(principal.tenant_id) as session:
                self._audit.record(
                    session,
                    principal.tenant_id,
                    "document.previewed",
                    actor=AuditActor(
                        principal.user_id, "user" if principal.kind == "session" else "api_key"
                    ),
                    object_type="document",
                    object_id=str(document_id),
                    details={"version": version_number, "page": number},
                )
        return Rendered(buffer.getvalue(), "image/png")

    @staticmethod
    def _pdf_page(path: Path, number: int, width: int) -> Image.Image:
        try:
            pdf = pdfium.PdfDocument(str(path))
        except pdfium.PdfiumError as exc:
            raise PreviewUnavailable("This file is damaged and cannot be shown.") from exc
        try:
            if number < 1 or number > min(len(pdf), MAX_PAGES):
                raise NotFound("That page does not exist.")
            page = pdf[number - 1]
            try:
                page_width, page_height = page.get_size()
                scale = min(width / max(page_width, 1), MAX_SIDE / max(page_width, page_height, 1))
                rendered: Image.Image = page.render(scale=scale).to_pil().convert("RGB")
                return rendered
            finally:
                page.close()
        finally:
            pdf.close()

    @staticmethod
    def _image_frame(path: Path, number: int, width: int) -> Image.Image:
        try:
            with Image.open(path) as source:
                frames = min(getattr(source, "n_frames", 1), MAX_PAGES)
                if number < 1 or number > frames:
                    raise NotFound("That page does not exist.")
                source.seek(number - 1)
                image = ImageOps.exif_transpose(source).convert("RGB")
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise PreviewUnavailable("This file is damaged and cannot be shown.") from exc
        if image.width > width:
            image = image.resize((width, max(1, round(image.height * width / image.width))))
        return image
