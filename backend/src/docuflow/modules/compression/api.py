"""Smaller copies of files over HTTP: ask for one, see how it did, download it, read the report."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from docuflow.modules.compression.service import CompressionService, DerivativeView, Report
from docuflow.modules.identity.api import Ctx, User_

router = APIRouter(prefix="/api/v1", tags=["compression"])


def get_compression(request: Request) -> CompressionService:
    service: CompressionService = request.app.state.compression
    return service


Comp = Annotated[CompressionService, Depends(get_compression)]


class RunOut(BaseModel):
    version_number: int


class PageOut(BaseModel):
    page: int
    kind: str
    treatment: str
    ssim: float
    psnr: float
    threshold: float
    passed: bool


class DerivativeOut(BaseModel):
    id: uuid.UUID
    version_number: int
    current_version: bool
    # accepted, rejected or no_benefit. Only an accepted copy can be recommended or downloaded.
    status: str
    recommended: bool
    reason: str | None
    mime: str | None
    source_size: int
    derivative_size: int
    reduction: float | None
    worst_ssim: float | None
    mean_ssim: float | None
    seconds: float | None
    profile: str | None
    dpi: int | None
    pages: list[PageOut]
    created_at: datetime


class ReportOut(BaseModel):
    runs: dict[str, int]
    accepted_source_bytes: int
    accepted_copy_bytes: int
    # How much smaller the accepted copies are than their originals.
    copy_saving_bytes: int
    average_reduction: float | None
    # Storage given back. Zero, because originals are kept. Never add it to the saving above.
    storage_reclaimed_bytes: int
    rows: list[dict[str, Any]]


def _derivative(view: DerivativeView) -> DerivativeOut:
    d = view.derivative
    m = d.metrics or {}
    p = d.params or {}
    return DerivativeOut(
        id=d.id,
        version_number=view.version_number,
        current_version=view.current_version,
        status=d.status,
        recommended=view.recommended,
        reason=d.reason,
        mime=d.mime,
        source_size=d.source_size,
        derivative_size=d.derivative_size,
        reduction=m.get("reduction"),
        worst_ssim=m.get("worst_ssim"),
        mean_ssim=m.get("mean_ssim"),
        seconds=m.get("seconds"),
        profile=p.get("profile"),
        dpi=p.get("dpi"),
        pages=[PageOut(**item) for item in m.get("per_page", [])],
        created_at=d.created_at,
    )


def _report(report: Report) -> ReportOut:
    return ReportOut(
        runs=report.runs,
        accepted_source_bytes=report.accepted_source_bytes,
        accepted_copy_bytes=report.accepted_copy_bytes,
        copy_saving_bytes=report.copy_saving_bytes,
        average_reduction=report.average_reduction,
        storage_reclaimed_bytes=report.storage_reclaimed_bytes,
        rows=report.rows,
    )


@router.post("/documents/{document_id}/compression/run", status_code=202)
def run_compression(document_id: uuid.UUID, ctx: Ctx, comp: Comp, who: User_) -> RunOut:
    """Makes a smaller copy of the current version in the background. The original is untouched."""
    return RunOut(version_number=comp.request_run(ctx.services.db, who, document_id))


@router.get("/documents/{document_id}/compression")
def list_compression(
    document_id: uuid.UUID, ctx: Ctx, comp: Comp, who: User_
) -> list[DerivativeOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_derivative(v) for v in comp.list(session, who, document_id)]


@router.get("/documents/{document_id}/compression/{derivative_id}/download")
def download_copy(
    document_id: uuid.UUID, derivative_id: uuid.UUID, ctx: Ctx, comp: Comp, who: User_
) -> StreamingResponse:
    """A copy that passed the quality check, as an attachment. Needs download and is audited."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        copy = comp.prepare_download(session, who, document_id, derivative_id)
    ascii_name = "".join(
        c if 32 <= ord(c) < 127 and c not in '";\\' else "_" for c in copy.filename
    )
    return StreamingResponse(
        comp.read(copy),
        media_type=copy.mime,
        headers={
            "Content-Length": str(copy.size_bytes),
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(copy.filename)}"
            ),
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/compression/report", response_model=None)
def compression_report(
    ctx: Ctx,
    comp: Comp,
    who: User_,
    format: Literal["json", "csv"] = "json",
) -> ReportOut | StreamingResponse | JSONResponse:
    """Runs, savings and the measurements behind them. Needs the audit.read right."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        report = comp.report(session, who)
    if format == "csv":
        return StreamingResponse(
            comp.report_csv(report),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="compression-report.csv"',
                "Cache-Control": "private, no-store",
            },
        )
    return _report(report)
