"""Documents HTTP API: upload as a raw body, versions and audited download."""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Callable
from typing import Annotated, BinaryIO, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from docuflow.modules.documents.extraction import ExtractionService
from docuflow.modules.documents.imports import ImportService, ImportView
from docuflow.modules.documents.preview import PreviewService
from docuflow.modules.documents.schemas import (
    ImportItemOut,
    ImportOut,
    LanguagesOut,
    PageTextOut,
    PreviewOut,
    PreviewPageOut,
    TextOut,
    TextRequestOut,
    UploadResult,
    VersionOut,
    VersionResult,
)
from docuflow.modules.documents.service import (
    DocumentService,
    Download,
    FileTooLarge,
    VersionAdded,
    VersionInfo,
)
from docuflow.modules.identity.api import Ctx, User_
from docuflow.modules.repository.schemas import DocumentOut
from docuflow.modules.repository.service import Destination
from docuflow.platform.errors import DomainError

router = APIRouter(prefix="/api/v1", tags=["documents"])
UNSAFE_IN_HEADER = {chr(34), chr(92), ";"}


def get_documents(request: Request) -> DocumentService:
    service: DocumentService = request.app.state.documents
    return service


def get_text(request: Request) -> ExtractionService:
    service: ExtractionService = request.app.state.text
    return service


def get_preview(request: Request) -> PreviewService:
    service: PreviewService = request.app.state.preview
    return service


def get_imports(request: Request) -> ImportService:
    service: ImportService = request.app.state.imports
    return service


Docs = Annotated[DocumentService, Depends(get_documents)]
Imports = Annotated[ImportService, Depends(get_imports)]
Text = Annotated[ExtractionService, Depends(get_text)]
Preview = Annotated[PreviewService, Depends(get_preview)]
Filename = Annotated[str, Query(min_length=1, max_length=300)]
Reason = Annotated[str, Query(max_length=500)]


async def _with_body[T](
    request: Request,
    limit: int,
    noun: str,
    check: Callable[[], None],
    work: Callable[[BinaryIO], T],
) -> T:
    """Receives the body into a private temporary file, then hands it to the work."""
    # Refuse before receiving a large body the caller may not send.
    await run_in_threadpool(check)
    too_large = FileTooLarge(f"{noun} can be at most {limit // (1024 * 1024)} MB.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise too_large
    with tempfile.TemporaryFile() as spool:
        received = 0
        async for part in request.stream():
            received += len(part)
            if received > limit:
                raise too_large
            spool.write(part)
        if received == 0:
            raise DomainError("The file is empty.")
        spool.seek(0)
        return await run_in_threadpool(work, cast("BinaryIO", spool))


def _version_out(info: VersionInfo) -> VersionOut:
    return VersionOut(
        id=info.version.id,
        number=info.version.number,
        original_filename=info.version.original_filename,
        state=info.version.state,
        reason=info.version.reason,
        scan_engine=info.version.scan_engine,
        text_state=info.version.text_state,
        size_bytes=info.size_bytes,
        mime=info.mime,
        created_at=info.version.created_at,
    )


async def _upload(
    request: Request, ctx: Ctx, docs: DocumentService, who: User_, where: Destination, filename: str
) -> UploadResult:
    db = ctx.services.db
    result = await _with_body(
        request,
        docs.max_upload_bytes,
        "Files",
        lambda: docs.check_can_upload(db, who, where),
        lambda body: docs.upload(db, who, where, filename, body),
    )
    with db.tenant_session(who.tenant_id) as session:
        info = next(
            item
            for item in docs.versions(session, who, result.document.id)
            if item.version.id == result.version.id
        )
    return UploadResult(
        document=DocumentOut.model_validate(result.document),
        version=_version_out(info),
        duplicate=result.duplicate,
        duplicate_of=result.duplicate_of,
    )


@router.post("/cabinets/{cabinet_id}/documents", status_code=201)
async def upload_to_cabinet(
    cabinet_id: uuid.UUID,
    filename: Filename,
    request: Request,
    ctx: Ctx,
    docs: Docs,
    who: User_,
) -> UploadResult:
    return await _upload(request, ctx, docs, who, Destination(cabinet_id=cabinet_id), filename)


@router.post("/folders/{folder_id}/documents", status_code=201)
async def upload_to_folder(
    folder_id: uuid.UUID,
    filename: Filename,
    request: Request,
    ctx: Ctx,
    docs: Docs,
    who: User_,
) -> UploadResult:
    return await _upload(request, ctx, docs, who, Destination(folder_id=folder_id), filename)


@router.get("/documents/{document_id}/versions")
def list_versions(document_id: uuid.UUID, ctx: Ctx, docs: Docs, who: User_) -> list[VersionOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_version_out(item) for item in docs.versions(session, who, document_id)]


@router.post("/documents/{document_id}/versions", status_code=201)
async def add_version(
    document_id: uuid.UUID,
    filename: Filename,
    request: Request,
    ctx: Ctx,
    docs: Docs,
    who: User_,
    reason: Reason = "",
) -> VersionResult:
    """Adds a new version. The body is the file; earlier versions stay downloadable."""
    db = ctx.services.db
    added: VersionAdded = await _with_body(
        request,
        docs.max_upload_bytes,
        "Files",
        lambda: docs.check_can_add_version(db, who, document_id),
        lambda body: docs.add_version(db, who, document_id, filename, body, reason),
    )
    with db.tenant_session(who.tenant_id) as session:
        info = next(
            item
            for item in docs.versions(session, who, document_id)
            if item.version.id == added.version.id
        )
    return VersionResult(
        document=DocumentOut.model_validate(added.document),
        version=_version_out(info),
        duplicate=added.duplicate,
    )


def _attachment(docs: DocumentService, info: Download) -> StreamingResponse:
    ascii_name = "".join(
        c if 32 <= ord(c) < 127 and c not in UNSAFE_IN_HEADER else "_" for c in info.filename
    )
    return StreamingResponse(
        docs.read(info),
        media_type=info.mime,
        headers={
            "Content-Length": str(info.size_bytes),
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(info.filename)}"
            ),
        },
    )


@router.get("/documents/{document_id}/download")
def download(document_id: uuid.UUID, ctx: Ctx, docs: Docs, who: User_) -> StreamingResponse:
    """The current original, as an attachment. Needs the download permission and is audited."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        info = docs.prepare_download(session, who, document_id)
    return _attachment(docs, info)


@router.get("/documents/{document_id}/versions/{number}/download")
def download_version(
    document_id: uuid.UUID, number: int, ctx: Ctx, docs: Docs, who: User_
) -> StreamingResponse:
    """An earlier original, with the same permission and audit as the current one."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        info = docs.prepare_download(session, who, document_id, number)
    return _attachment(docs, info)


def _import_out(view: ImportView) -> ImportOut:
    job = view.job
    return ImportOut(
        id=job.id,
        status=job.status,
        archive_name=job.archive_name,
        file_count=job.file_count,
        filed=view.filed,
        duplicates=view.duplicates,
        failed=view.failed,
        error=job.error,
        created_at=job.created_at,
        finished_at=job.finished_at,
        items=[ImportItemOut(**vars(item)) for item in view.items],
    )


async def _submit_import(
    request: Request,
    ctx: Ctx,
    imports: ImportService,
    who: User_,
    where: Destination,
    filename: str,
) -> ImportOut:
    db = ctx.services.db
    job_id = await _with_body(
        request,
        imports.limits.max_archive_bytes,
        "Archives",
        lambda: imports.check_can_submit(db, who, where),
        lambda body: imports.submit(db, who, where, filename, body),
    )
    with db.tenant_session(who.tenant_id) as session:
        return _import_out(imports.get(session, who, job_id, items=0))


@router.post("/cabinets/{cabinet_id}/imports", status_code=202)
async def import_into_cabinet(
    cabinet_id: uuid.UUID,
    filename: Filename,
    request: Request,
    ctx: Ctx,
    imports: Imports,
    who: User_,
) -> ImportOut:
    """Queues a ZIP archive for import. The body is the archive; poll GET /imports/{id}."""
    return await _submit_import(
        request, ctx, imports, who, Destination(cabinet_id=cabinet_id), filename
    )


@router.post("/folders/{folder_id}/imports", status_code=202)
async def import_into_folder(
    folder_id: uuid.UUID,
    filename: Filename,
    request: Request,
    ctx: Ctx,
    imports: Imports,
    who: User_,
) -> ImportOut:
    return await _submit_import(
        request, ctx, imports, who, Destination(folder_id=folder_id), filename
    )


@router.get("/imports/{import_id}")
def get_import(import_id: uuid.UUID, ctx: Ctx, imports: Imports, who: User_) -> ImportOut:
    """Progress and results of an import. Only the person who started it can read it."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _import_out(imports.get(session, who, import_id))


@router.get("/ocr/languages")
def ocr_languages(text: Text, who: User_) -> LanguagesOut:
    """The languages this server can read, and the defaults used when none is chosen."""
    info = text.language_info()
    return LanguagesOut(**vars(info))


@router.get("/documents/{document_id}/text")
def document_text(
    document_id: uuid.UUID,
    ctx: Ctx,
    text: Text,
    who: User_,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TextOut:
    """The text of the current version, page by page. Needs the preview right and is audited."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        view = text.read(session, who, document_id, limit, offset)
    return TextOut(
        version=view.version_number,
        state=view.state,
        error=view.error,
        total_pages=view.total_pages,
        pages=[PageTextOut(**vars(page)) for page in view.pages],
    )


@router.post("/documents/{document_id}/text/reprocess", status_code=202)
async def reprocess_text(
    document_id: uuid.UUID,
    ctx: Ctx,
    text: Text,
    who: User_,
    languages: Annotated[str, Query(max_length=200)] = "",
) -> TextRequestOut:
    """Reads the current version again, in the languages given (for example eng,deu)."""
    version, state, chosen = await run_in_threadpool(
        text.reprocess, ctx.services.db, who, document_id, languages or None
    )
    return TextRequestOut(version=version, state=state, languages=chosen)


@router.get("/documents/{document_id}/preview")
def preview_info(document_id: uuid.UUID, ctx: Ctx, preview: Preview, who: User_) -> PreviewOut:
    """How to show the current version: as page images, as text, or not at all."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        view = preview.info(session, who, document_id)
    return PreviewOut(
        version=view.version_number,
        kind=view.kind,
        mime=view.mime,
        pages=[PreviewPageOut(**vars(page)) for page in view.pages],
    )


@router.get("/documents/{document_id}/preview/pages/{number}")
def preview_page(
    document_id: uuid.UUID,
    number: int,
    ctx: Ctx,
    preview: Preview,
    who: User_,
    width: Annotated[int, Query(ge=64, le=2000)] = 900,
) -> Response:
    """One page as an image. The file itself is never sent. Needs the preview right."""
    image = preview.render(ctx.services.db, who, document_id, number, width)
    return Response(
        content=image.data,
        media_type=image.media_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
