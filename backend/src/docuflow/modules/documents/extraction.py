"""Reading the text of a document version (F04).

Rules that hold throughout:
- Text that is already in the file is used as it is. A page is only recognised (OCR) when it has
  no usable text, so a mixed PDF is recognised on its scanned pages alone.
- Recognition can use several languages at once, chosen per run from what the server has
  installed. Reading again in other languages adds a result; the newest result is current.
- Reading is a background job on the ``ocr`` queue, started when a version is filed. A file that
  cannot be read ends as ``failed`` with a message, and never blocks filing.
- The text of a document is its content: reading it needs the ``preview`` right and is audited.
"""

from __future__ import annotations

import re
import tempfile
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pypdfium2 as pdfium
import structlog
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from docuflow.modules.documents.models import BinaryObject, DocumentVersion, OcrResult, Page
from docuflow.modules.documents.ocr import (
    OcrEngine,
    OcrFailed,
    OcrPage,
    OcrUnavailable,
    parse_languages,
)
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.service import RepositoryService
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import Database
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound
from docuflow.platform.jobs import JobContext, enqueue
from docuflow.platform.storage import EncryptedObjects

log = structlog.get_logger("docuflow.extraction")

JOB_TYPE = "documents.extract_text"
# Job type of the indexing module, named here so reading text never imports indexing.
FIELDS_JOB_TYPE = "indexing.extract_fields"
IMAGE_TYPES = {"image/png", "image/jpeg", "image/tiff", "image/gif", "image/webp"}
TEXT_TYPES = {"text/plain", "text/csv"}
SUPPORTED_TYPES = {"application/pdf", *IMAGE_TYPES, *TEXT_TYPES}
MIN_NATIVE_CHARACTERS = 25
MIN_NATIVE_WORD_SHARE = 0.6
MAX_PIXELS = 60_000_000
MAX_SIDE = 6000
BLANK_RANGE = 12
MAX_TEXT_BYTES = 5 * 1024 * 1024
READ = 1024 * 1024
NATIVE = "native"
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class UnreadableFile(Exception):
    """The file cannot be read at all. The message is safe to show."""


@dataclass(frozen=True)
class ExtractionSettings:
    default_languages: tuple[str, ...] = ("eng", "fra")
    max_languages: int = 5
    dpi: int = 300
    max_pages: int = 500


def extraction_settings(settings: Any) -> ExtractionSettings:
    defaults = ExtractionSettings()
    raw = getattr(settings, "ocr_default_languages", ",".join(defaults.default_languages))
    return ExtractionSettings(
        default_languages=tuple(parse_languages(raw)) or defaults.default_languages,
        max_languages=getattr(settings, "ocr_max_languages_per_run", defaults.max_languages),
        dpi=getattr(settings, "ocr_dpi", defaults.dpi),
        max_pages=getattr(settings, "ocr_max_pages", defaults.max_pages),
    )


@dataclass(frozen=True)
class Extracted:
    number: int
    width: int
    height: int
    has_native_text: bool
    is_blank: bool
    source: str  # "native" or the engine's name
    text: str
    words: list[dict[str, object]] = field(default_factory=list)
    confidence: float | None = None
    language: str = ""
    engine_version: str = ""
    duration_ms: int = 0


def is_reliable(text: str) -> bool:
    """Whether text found in a PDF is real text and not an empty layer or broken encoding."""
    stripped = re.sub(r"\s", "", text)
    if len(stripped) < MIN_NATIVE_CHARACTERS:
        return False
    readable = sum(1 for char in stripped if char.isalnum() or char in ".,;:-/()%€$£'\"&@#")
    return readable / len(stripped) >= MIN_NATIVE_WORD_SHARE


def is_blank(image: Image.Image) -> bool:
    low, high = cast("tuple[int, int]", image.convert("L").getextrema())
    return high - low < BLANK_RANGE


def request_extraction(
    session: Session, tenant_id: uuid.UUID, version: DocumentVersion, mime: str
) -> None:
    """Queues reading a new version, in the same transaction that filed it."""
    if mime not in SUPPORTED_TYPES:
        version.text_state = "unsupported"
        return
    version.text_state = "pending"
    enqueue(
        session,
        tenant_id,
        JOB_TYPE,
        f"text:{version.id}",
        queue="ocr",
        payload={"version_id": str(version.id)},
    )


class Extractor:
    """Turns a file into pages of text. The engine is only asked for pages that need it."""

    def __init__(
        self,
        engine: OcrEngine,
        languages: Sequence[str],
        settings: ExtractionSettings,
    ) -> None:
        self._engine = engine
        self._languages = list(languages)
        self._settings = settings

    def pages(self, path: Path, mime: str) -> Iterator[Extracted]:
        if mime == "application/pdf":
            return self._pdf(path)
        if mime in IMAGE_TYPES:
            return self._image(path)
        return self._text(path)

    def count(self, path: Path, mime: str) -> int:
        """How many pages the file has, for progress. Zero when it is not known."""
        try:
            if mime == "application/pdf":
                pdf = pdfium.PdfDocument(str(path))
                try:
                    return min(len(pdf), self._settings.max_pages)
                finally:
                    pdf.close()
            if mime in IMAGE_TYPES:
                with Image.open(path) as image:
                    return min(getattr(image, "n_frames", 1), self._settings.max_pages)
        except Exception:
            return 0
        return 1

    # Recognition

    def _recognise(self, image: Image.Image, number: int) -> Extracted:
        if not self._languages:
            raise OcrFailed("None of the chosen languages is installed on this server.")
        started = time.perf_counter()
        result: OcrPage = self._engine.recognise(image, self._languages)
        return Extracted(
            number=number,
            width=image.width,
            height=image.height,
            has_native_text=False,
            is_blank=False,
            source=self._engine.name,
            text=result.text,
            words=[
                {
                    "t": word.text,
                    "x": word.x,
                    "y": word.y,
                    "w": word.w,
                    "h": word.h,
                    "c": word.confidence,
                }
                for word in result.words
            ],
            confidence=result.confidence,
            language="+".join(self._languages),
            engine_version=self._engine.version(),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _blank_page(self, image: Image.Image, number: int) -> Extracted:
        return Extracted(
            number=number,
            width=image.width,
            height=image.height,
            has_native_text=False,
            is_blank=True,
            source=self._engine.name,
            text="",
            language="+".join(self._languages),
        )

    # Formats

    def _pdf(self, path: Path) -> Iterator[Extracted]:
        try:
            pdf = pdfium.PdfDocument(str(path))
        except pdfium.PdfiumError as exc:
            raise UnreadableFile("The PDF is damaged or protected with a password.") from exc
        try:
            for index in range(min(len(pdf), self._settings.max_pages)):
                page = pdf[index]
                try:
                    yield self._pdf_page(page, index + 1)
                finally:
                    page.close()
        finally:
            pdf.close()

    def _pdf_page(self, page: pdfium.PdfPage, number: int) -> Extracted:
        text_page = page.get_textpage()
        try:
            native = text_page.get_text_range()
        finally:
            text_page.close()
        width, height = page.get_size()
        if is_reliable(native):
            return Extracted(
                number=number,
                width=round(width),
                height=round(height),
                has_native_text=True,
                is_blank=False,
                source=NATIVE,
                text=native.strip(),
                confidence=1.0,
            )
        scale = min(self._settings.dpi / 72, MAX_SIDE / max(width, height, 1))
        image = page.render(scale=scale).to_pil()
        if is_blank(image):
            return self._blank_page(image, number)
        extracted = self._recognise(image, number)
        return Extracted(**{**extracted.__dict__, "has_native_text": bool(native.strip())})

    def _image(self, path: Path) -> Iterator[Extracted]:
        try:
            with Image.open(path) as source:
                for number, frame in enumerate(ImageSequence.Iterator(source), start=1):
                    if number > self._settings.max_pages:
                        return
                    image = ImageOps.exif_transpose(frame).convert("RGB")
                    if max(image.size) > MAX_SIDE:
                        image.thumbnail((MAX_SIDE, MAX_SIDE))
                    yield (
                        self._blank_page(image, number)
                        if is_blank(image)
                        else self._recognise(image, number)
                    )
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise UnreadableFile("The image is damaged or too large to read.") from exc

    @staticmethod
    def _text(path: Path) -> Iterator[Extracted]:
        raw = path.read_bytes()[:MAX_TEXT_BYTES]
        text = raw.decode("utf-8", errors="replace")
        yield Extracted(
            number=1,
            width=0,
            height=0,
            has_native_text=True,
            is_blank=not text.strip(),
            source=NATIVE,
            text=text.strip(),
            confidence=1.0,
        )


@dataclass(frozen=True)
class PageText:
    number: int
    source: str
    language: str
    confidence: float | None
    is_blank: bool
    text: str


@dataclass(frozen=True)
class TextView:
    version_number: int
    state: str
    error: str | None
    total_pages: int
    pages: list[PageText]


@dataclass(frozen=True)
class LanguageInfo:
    engine: str
    engine_version: str
    available: bool
    languages: list[str]
    defaults: list[str]
    max_per_run: int


class ExtractionService:
    def __init__(
        self,
        audit: AuditLog,
        policy: PolicyService,
        repo: RepositoryService,
        objects: EncryptedObjects,
        engine: OcrEngine,
        settings: ExtractionSettings,
    ) -> None:
        self._audit = audit
        self._policy = policy
        self._repo = repo
        self._objects = objects
        self._engine = engine
        self.settings = settings

    # Languages

    def language_info(self) -> LanguageInfo:
        available = self._engine.available()
        return LanguageInfo(
            engine=self._engine.name,
            engine_version=self._engine.version() if available else "",
            available=available,
            languages=self._engine.languages() if available else [],
            defaults=list(self.settings.default_languages),
            max_per_run=self.settings.max_languages,
        )

    def resolve_languages(self, raw: str | Sequence[str] | None) -> list[str]:
        """Languages for a run: those asked for, or the defaults, checked against the server."""
        chosen = parse_languages(raw) if raw else list(self.settings.default_languages)
        if len(chosen) > self.settings.max_languages:
            raise DomainError(
                f"Choose at most {self.settings.max_languages} languages for one run. "
                "Reading with many languages at once is slower and less accurate."
            )
        if not self._engine.available():
            raise OcrUnavailable("Reading scanned pages is not set up on this server.")
        installed = set(self._engine.languages())
        missing = [code for code in chosen if code not in installed]
        if missing:
            raise DomainError(
                "These languages are not installed on this server: " + ", ".join(missing) + "."
            )
        return chosen

    # Reading the result

    def read(
        self,
        session: Session,
        principal: Principal,
        document_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> TextView:
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
        pages = list(
            session.scalars(
                select(Page)
                .where(Page.version_id == version.id)
                .order_by(Page.number)
                .limit(limit)
                .offset(offset)
            )
        )
        total = len(session.scalars(select(Page.id).where(Page.version_id == version.id)).all())
        latest: dict[uuid.UUID, OcrResult] = {}
        if pages:
            rows = session.scalars(
                select(OcrResult)
                .where(OcrResult.page_id.in_([page.id for page in pages]))
                .order_by(OcrResult.created_at, OcrResult.id)
            )
            for row in rows:
                latest[row.page_id] = row  # the newest row comes last
        items = [
            PageText(
                page.number,
                latest[page.id].engine if page.id in latest else "",
                latest[page.id].language if page.id in latest else "",
                latest[page.id].confidence if page.id in latest else None,
                page.is_blank,
                latest[page.id].text if page.id in latest else "",
            )
            for page in pages
        ]
        self._audit.record(
            session,
            principal.tenant_id,
            "document.text_viewed",
            actor=AuditActor(
                principal.user_id, "user" if principal.kind == "session" else "api_key"
            ),
            object_type="document",
            object_id=str(document_id),
            details={"version": version.number, "offset": offset, "pages": len(items)},
        )
        return TextView(version.number, version.text_state, version.text_error, total, items)

    # Reading again

    def reprocess(
        self,
        db: Database,
        principal: Principal,
        document_id: uuid.UUID,
        languages: str | Sequence[str] | None,
    ) -> tuple[int, str, list[str]]:
        """Reads the current version again, in the languages asked for."""
        chosen = self.resolve_languages(languages)
        with db.tenant_session(principal.tenant_id) as session:
            document = self._repo.document_for_change(
                session, principal, document_id, "edit_metadata", lock=True
            )
            version = (
                session.get(DocumentVersion, document.current_version_id, with_for_update=True)
                if document.current_version_id
                else None
            )
            if version is None:
                raise NotFound("That document has no file.")
            binary = session.get(BinaryObject, version.binary_object_id)
            if binary is None or binary.mime not in SUPPORTED_TYPES:
                raise Conflict("That kind of file has no text to read yet.")
            if version.text_state in ("pending", "running"):
                raise Conflict("That file is already being read. Try again when it is done.")
            version.text_state = "pending"
            version.text_error = None
            enqueue(
                session,
                principal.tenant_id,
                JOB_TYPE,
                f"text:{version.id}:{uuid.uuid4().hex}",
                queue="ocr",
                payload={"version_id": str(version.id), "languages": chosen},
            )
            self._audit.record(
                session,
                principal.tenant_id,
                "document.text_requested",
                actor=AuditActor(
                    principal.user_id, "user" if principal.kind == "session" else "api_key"
                ),
                object_type="document",
                object_id=str(document_id),
                details={"version": version.number, "languages": chosen},
            )
            return version.number, version.text_state, chosen

    # Running (called by the worker)

    def run(self, db: Database, ctx: JobContext) -> None:
        version_id = uuid.UUID(ctx.payload["version_id"])
        tenant_id = ctx.tenant_id
        asked: list[str] | None = ctx.payload.get("languages")
        with db.tenant_session(tenant_id) as session:
            version = session.scalar(
                select(DocumentVersion).where(DocumentVersion.id == version_id).with_for_update()
            )
            if version is None or version.text_state not in ("pending", "running"):
                return
            binary = session.get(BinaryObject, version.binary_object_id)
            if binary is None:
                return
            version.text_state = "running"
            version.text_error = None
            document_id = version.document_id
            number = version.number
            key, wrapped, mime = binary.object_key, binary.wrapped_data_key, binary.mime
        languages = self._usable_languages(asked)
        error: str | None = None
        counts = {"pages": 0, "recognised": 0}
        try:
            with tempfile.TemporaryDirectory(prefix="docuflow-text-") as folder:
                path = Path(folder) / "source"
                with path.open("wb") as handle:
                    for chunk in self._objects.read(tenant_id, key, wrapped):
                        handle.write(chunk)
                extractor = Extractor(self._engine, languages, self.settings)
                total = max(1, extractor.count(path, mime))
                for extracted in extractor.pages(path, mime):
                    self._store(db, tenant_id, version_id, extracted)
                    counts["pages"] += 1
                    counts["recognised"] += 0 if extracted.source == NATIVE else 1
                    ctx.report(int(100 * counts["pages"] / total))
        except (UnreadableFile, OcrFailed) as exc:
            error = str(exc)
        except OcrUnavailable as exc:
            error = exc.message
        except Exception:
            log.exception("text_extraction_failed", version_id=str(version_id))
            error = "The file could not be read."
        self._finish(db, tenant_id, version_id, document_id, number, languages, counts, error)

    def _usable_languages(self, asked: list[str] | None) -> list[str]:
        wanted = asked or list(self.settings.default_languages)
        if not self._engine.available():
            return wanted
        installed = set(self._engine.languages())
        return [code for code in wanted if code in installed]

    @staticmethod
    def _store(db: Database, tenant_id: uuid.UUID, version_id: uuid.UUID, item: Extracted) -> None:
        with db.tenant_session(tenant_id) as session:
            page = session.scalar(
                select(Page).where(Page.version_id == version_id, Page.number == item.number)
            )
            if page is None:
                page = Page(tenant_id=tenant_id, version_id=version_id, number=item.number)
                session.add(page)
            page.width, page.height = item.width, item.height
            page.has_native_text = item.has_native_text
            page.is_blank = item.is_blank
            session.flush()
            if item.is_blank:
                return
            result = session.scalar(
                select(OcrResult).where(
                    OcrResult.page_id == page.id,
                    OcrResult.engine == item.source,
                    OcrResult.language == item.language,
                )
            )
            if result is None:
                result = OcrResult(
                    tenant_id=tenant_id,
                    page_id=page.id,
                    engine=item.source,
                    language=item.language,
                )
                session.add(result)
            result.engine_version = item.engine_version
            result.text = item.text
            result.boxes = item.words
            result.confidence = item.confidence
            result.duration_ms = item.duration_ms

    def _finish(
        self,
        db: Database,
        tenant_id: uuid.UUID,
        version_id: uuid.UUID,
        document_id: uuid.UUID,
        number: int,
        languages: list[str],
        counts: dict[str, int],
        error: str | None,
    ) -> None:
        with db.tenant_session(tenant_id) as session:
            version = session.get(DocumentVersion, version_id)
            assert version is not None
            version.text_state = "failed" if error else "done"
            version.text_error = error
            if not error:
                enqueue(
                    session,
                    tenant_id,
                    FIELDS_JOB_TYPE,
                    f"fields:{version_id}:{uuid.uuid4().hex}",
                    queue="index",
                    payload={"version_id": str(version_id)},
                )
            self._audit.record(
                session,
                tenant_id,
                "document.text_failed" if error else "document.text_extracted",
                actor=AuditActor(),
                object_type="document",
                object_id=str(document_id),
                details={
                    "version": number,
                    "pages": counts["pages"],
                    "recognised_pages": counts["recognised"],
                    "languages": languages,
                    "error": error,
                },
            )
