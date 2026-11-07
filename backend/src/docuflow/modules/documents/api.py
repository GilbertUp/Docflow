"""Documents HTTP API: upload as a raw body, versions and audited download."""

from __future__ import annotations

import tempfile
import uuid
from typing import Annotated, BinaryIO, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from docuflow.modules.documents.schemas import UploadResult, VersionOut
from docuflow.modules.documents.service import DocumentService, FileTooLarge
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


async def _upload(
    request: Request, ctx: Ctx, docs: DocumentService, who: User_, where: Destination, filename: str
) -> UploadResult:
    # Refuse before receiving a large body the caller may not send.
    await run_in_threadpool(docs.check_can_upload, ctx.services.db, who, where)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > docs.max_upload_bytes:
        raise FileTooLarge(f"Files can be at most {docs.max_upload_bytes // (1024 * 1024)} MB.")
    with tempfile.TemporaryFile() as spool:
        received = 0
        async for part in request.stream():
            received += len(part)
            if received > docs.max_upload_bytes:
                raise FileTooLarge(
                    f"Files can be at most {docs.max_upload_bytes // (1024 * 1024)} MB."
                )
            spool.write(part)
        if received == 0:
            raise DomainError("The file is empty.")
        spool.seek(0)
        result = await run_in_threadpool(
            docs.upload, ctx.services.db, who, where, filename, cast("BinaryIO", spool)
        )
    return UploadResult(
        document=DocumentOut.model_validate(result.document),
        version=VersionOut.model_validate(result.version),
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
        return [
            VersionOut.model_validate(item) for item in docs.versions(session, who, document_id)
        ]


@router.get("/documents/{document_id}/download")
def download(document_id: uuid.UUID, ctx: Ctx, docs: Docs, who: User_) -> StreamingResponse:
    """The original bytes, as an attachment. Needs the download permission and is audited."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        info = docs.prepare_download(session, who, document_id)
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
