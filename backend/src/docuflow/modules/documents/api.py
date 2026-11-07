"""Documents HTTP API: upload as a raw body, versions and audited download."""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Callable
from typing import Annotated, BinaryIO, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from docuflow.modules.documents.schemas import UploadResult, VersionOut, VersionResult
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


Docs = Annotated[DocumentService, Depends(get_documents)]
Filename = Annotated[str, Query(min_length=1, max_length=300)]
Reason = Annotated[str, Query(max_length=500)]


async def _with_body[T](
    request: Request,
    docs: DocumentService,
    check: Callable[[], None],
    work: Callable[[BinaryIO], T],
) -> T:
    """Receives the body into a private temporary file, then hands it to the work."""
    # Refuse before receiving a large body the caller may not send.
    await run_in_threadpool(check)
    too_large = FileTooLarge(f"Files can be at most {docs.max_upload_bytes // (1024 * 1024)} MB.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > docs.max_upload_bytes:
        raise too_large
    with tempfile.TemporaryFile() as spool:
        received = 0
        async for part in request.stream():
            received += len(part)
            if received > docs.max_upload_bytes:
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
        docs,
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
        docs,
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
