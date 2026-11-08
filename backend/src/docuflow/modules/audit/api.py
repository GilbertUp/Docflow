"""The audit trail over HTTP: search, per-document timeline, export and verification."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from docuflow.modules.audit.service import AuditQueryService, EventView, Filters, label_of
from docuflow.modules.identity.api import Ctx, User_

router = APIRouter(prefix="/api/v1", tags=["audit"])


def get_audit(request: Request) -> AuditQueryService:
    service: AuditQueryService = request.app.state.audit_query
    return service


Aud = Annotated[AuditQueryService, Depends(get_audit)]


class AuditEventOut(BaseModel):
    seq: int
    occurred_at: datetime
    actor_id: uuid.UUID | None
    actor_name: str | None
    actor_type: str
    action: str
    label: str
    object_type: str | None
    object_id: str | None
    result: str
    details: dict[str, Any]


class AuditEventsOut(BaseModel):
    events: list[AuditEventOut]
    # Pass this as before_seq to get the next, older, page. Null at the end.
    next_before_seq: int | None


class VerifyOut(BaseModel):
    ok: bool
    events: int
    first_bad_seq: int | None
    reason: str | None
    checkpoints_ok: bool | None
    checkpoints: int | None
    checkpoint_reason: str | None
    head_hash: str | None


def _event(view: EventView) -> AuditEventOut:
    e = view.event
    return AuditEventOut(
        seq=e.seq,
        occurred_at=e.occurred_at,
        actor_id=e.actor_id,
        actor_name=view.actor_name,
        actor_type=e.actor_type,
        action=e.action,
        label=label_of(e.action),
        object_type=e.object_type,
        object_id=e.object_id,
        result=e.result,
        details=dict(e.details),
    )


def _filters(
    actor_id: uuid.UUID | None,
    action: str | None,
    action_prefix: str | None,
    object_type: str | None,
    object_id: str | None,
    result: str | None,
    since: datetime | None,
    until: datetime | None,
) -> Filters:
    return Filters(actor_id, action, action_prefix, object_type, object_id, result, since, until)


@router.get("/audit/events")
def search_events(
    ctx: Ctx,
    aud: Aud,
    who: User_,
    actor_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=120)] = None,
    action_prefix: Annotated[str | None, Query(max_length=120)] = None,
    object_type: Annotated[str | None, Query(max_length=80)] = None,
    object_id: Annotated[str | None, Query(max_length=64)] = None,
    result: Annotated[str | None, Query(max_length=20)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
) -> AuditEventsOut:
    """Events, newest first. Needs the audit.read right."""
    filters = _filters(
        actor_id, action, action_prefix, object_type, object_id, result, since, until
    )
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        page = aud.search(session, who, filters, limit=limit, before_seq=before_seq)
        return AuditEventsOut(
            events=[_event(v) for v in page.events], next_before_seq=page.next_before_seq
        )


@router.get("/documents/{document_id}/audit")
def document_timeline(
    document_id: uuid.UUID,
    ctx: Ctx,
    aud: Aud,
    who: User_,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[AuditEventOut]:
    """Everything recorded about one document. Needs audit.read and the right to read it."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_event(v) for v in aud.timeline(session, who, document_id, limit)]


@router.get("/audit/export")
def export_events(
    ctx: Ctx,
    aud: Aud,
    who: User_,
    actor_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=120)] = None,
    action_prefix: Annotated[str | None, Query(max_length=120)] = None,
    object_type: Annotated[str | None, Query(max_length=80)] = None,
    object_id: Annotated[str | None, Query(max_length=64)] = None,
    result: Annotated[str | None, Query(max_length=20)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> StreamingResponse:
    """The matching events as CSV, oldest first. The export is itself recorded."""
    filters = _filters(
        actor_id, action, action_prefix, object_type, object_id, result, since, until
    )
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        export = aud.export(session, who, filters)
    return StreamingResponse(
        export.rows,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="audit-export.csv"',
            "X-Export-Events": str(export.count),
            "X-Export-Truncated": "true" if export.truncated else "false",
            "X-Chain-Head-Seq": str(export.head_seq),
            "X-Chain-Head-Hash": export.head_hash,
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/audit/verify")
def verify_trail(ctx: Ctx, aud: Aud, who: User_) -> VerifyOut:
    """Checks every event's hash and link, and the chain against the external checkpoints."""
    store = getattr(ctx.services, "store", None)
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        chain, anchors, _total = aud.verify(session, who, store)
    return VerifyOut(
        ok=chain.ok and (anchors is None or anchors.ok),
        events=chain.checked,
        first_bad_seq=chain.first_bad_seq,
        reason=chain.reason,
        checkpoints_ok=None if anchors is None else anchors.ok,
        checkpoints=None if anchors is None else anchors.checked,
        checkpoint_reason=None if anchors is None else anchors.reason,
        head_hash=chain.head_hash,
    )
