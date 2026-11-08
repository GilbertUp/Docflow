"""Retention classes, legal holds and disposal over HTTP."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from docuflow.modules.identity.api import Ctx, User_
from docuflow.modules.retention.service import (
    HoldView,
    RetentionService,
    RetentionView,
)

router = APIRouter(prefix="/api/v1", tags=["retention"])


def get_retention(request: Request) -> RetentionService:
    service: RetentionService = request.app.state.retention
    return service


Ret = Annotated[RetentionService, Depends(get_retention)]


class ClassOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    duration_days: int
    trigger: str
    status: str


class CreateClassRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    duration_days: int = Field(ge=1, le=36500)
    trigger: Literal["created", "last_version"] = "created"


class UpdateClassRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    duration_days: int | None = Field(default=None, ge=1, le=36500)
    status: Literal["active", "archived"] | None = None


class HoldRef(BaseModel):
    id: uuid.UUID
    name: str


class RetentionOut(BaseModel):
    document_id: uuid.UUID
    retention_class: ClassOut | None
    eligible_at: datetime | None
    # not_assigned, retained, eligible or held
    status: str
    holds: list[HoldRef]


class SetRetentionRequest(BaseModel):
    class_id: uuid.UUID | None


class HoldOut(BaseModel):
    id: uuid.UUID
    name: str
    reason: str
    status: str
    documents: int
    created_at: datetime
    released_at: datetime | None
    release_reason: str | None


class CreateHoldRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class ReleaseHoldRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class AddToHoldRequest(BaseModel):
    document_id: uuid.UUID


class HeldDocumentOut(BaseModel):
    id: uuid.UUID
    title: str


class HeldDocumentsOut(BaseModel):
    # Only the documents the caller may read, and how many there are in all.
    readable: list[HeldDocumentOut]
    total: int


class CandidateOut(BaseModel):
    document_id: uuid.UUID
    title: str
    retention_class: str
    eligible_at: datetime


class DisposeRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)


class DisposalOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    title: str
    retention_class: str
    versions: int
    reason: str | None
    approver_id: uuid.UUID
    created_at: datetime


def _class(row: object) -> ClassOut:
    return ClassOut(
        id=row.id,  # type: ignore[attr-defined]
        name=row.name,  # type: ignore[attr-defined]
        description=row.description,  # type: ignore[attr-defined]
        duration_days=row.duration_days,  # type: ignore[attr-defined]
        trigger=row.trigger,  # type: ignore[attr-defined]
        status=row.status,  # type: ignore[attr-defined]
    )


def _retention(view: RetentionView) -> RetentionOut:
    return RetentionOut(
        document_id=view.document.id,
        retention_class=_class(view.retention_class) if view.retention_class else None,
        eligible_at=view.eligible_at,
        status=view.status,
        holds=[HoldRef(id=h.id, name=h.name) for h in view.holds],
    )


def _hold(view: HoldView) -> HoldOut:
    row = view.hold
    return HoldOut(
        id=row.id,
        name=row.name,
        reason=row.reason,
        status=row.status,
        documents=view.documents,
        created_at=row.created_at,
        released_at=row.released_at,
        release_reason=row.release_reason,
    )


@router.get("/retention-classes")
def list_classes(ctx: Ctx, ret: Ret, who: User_, include_archived: bool = False) -> list[ClassOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_class(c) for c in ret.classes(session, include_archived)]


@router.post("/retention-classes", status_code=201)
def create_class(body: CreateClassRequest, ctx: Ctx, ret: Ret, who: User_) -> ClassOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _class(
            ret.create_class(
                session, who, body.name, body.description, body.duration_days, body.trigger
            )
        )


@router.patch("/retention-classes/{class_id}")
def update_class(
    class_id: uuid.UUID, body: UpdateClassRequest, ctx: Ctx, ret: Ret, who: User_
) -> ClassOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _class(
            ret.update_class(
                session,
                who,
                class_id,
                name=body.name,
                duration_days=body.duration_days,
                status=body.status,
            )
        )


@router.get("/documents/{document_id}/retention")
def get_document_retention(document_id: uuid.UUID, ctx: Ctx, ret: Ret, who: User_) -> RetentionOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _retention(ret.retention_of(session, who, document_id))


@router.put("/documents/{document_id}/retention")
def set_document_retention(
    document_id: uuid.UUID, body: SetRetentionRequest, ctx: Ctx, ret: Ret, who: User_
) -> RetentionOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _retention(ret.set_document_class(session, who, document_id, body.class_id))


@router.get("/legal-holds")
def list_holds(ctx: Ctx, ret: Ret, who: User_, include_released: bool = False) -> list[HoldOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_hold(h) for h in ret.holds(session, who, include_released)]


@router.post("/legal-holds", status_code=201)
def create_hold(body: CreateHoldRequest, ctx: Ctx, ret: Ret, who: User_) -> HoldOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _hold(ret.create_hold(session, who, body.name, body.reason))


@router.post("/legal-holds/{hold_id}/release")
def release_hold(
    hold_id: uuid.UUID, body: ReleaseHoldRequest, ctx: Ctx, ret: Ret, who: User_
) -> HoldOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _hold(ret.release_hold(session, who, hold_id, body.reason))


@router.get("/legal-holds/{hold_id}/documents")
def hold_documents(hold_id: uuid.UUID, ctx: Ctx, ret: Ret, who: User_) -> HeldDocumentsOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        readable, total = ret.hold_documents(session, who, hold_id)
        return HeldDocumentsOut(
            readable=[HeldDocumentOut(id=d.id, title=d.title) for d in readable], total=total
        )


@router.post("/legal-holds/{hold_id}/documents", status_code=204)
def add_to_hold(
    hold_id: uuid.UUID, body: AddToHoldRequest, ctx: Ctx, ret: Ret, who: User_
) -> Response:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ret.add_to_hold(session, who, hold_id, body.document_id)
    return Response(status_code=204)


@router.delete("/legal-holds/{hold_id}/documents/{document_id}", status_code=204)
def remove_from_hold(
    hold_id: uuid.UUID, document_id: uuid.UUID, ctx: Ctx, ret: Ret, who: User_
) -> Response:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ret.remove_from_hold(session, who, hold_id, document_id)
    return Response(status_code=204)


@router.get("/disposal/candidates")
def candidates(ctx: Ctx, ret: Ret, who: User_) -> list[CandidateOut]:
    """Documents past their retention, not on hold, that the caller may dispose of."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            CandidateOut(
                document_id=c.document.id,
                title=c.document.title,
                retention_class=c.class_name,
                eligible_at=c.eligible_at,
            )
            for c in ret.candidates(session, who)
        ]


@router.post("/documents/{document_id}/dispose")
def dispose(
    document_id: uuid.UUID, body: DisposeRequest, ctx: Ctx, ret: Ret, who: User_
) -> DisposalOut:
    """Destroys a document that is due. This cannot be undone. A permanent record is kept."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        done = ret.dispose(session, who, document_id, body.reason)
        out = DisposalOut(
            id=done.event.id,
            document_id=done.event.document_id,
            title=done.event.title,
            retention_class=done.event.retention_class,
            versions=len(done.event.version_hashes),
            reason=done.event.reason,
            approver_id=done.event.approver_id,
            created_at=done.event.created_at,
        )
    # The records are saved first. Removing the stored files is the last step.
    ret.purge(done.object_keys)
    return out


@router.get("/disposal/events")
def disposal_events(ctx: Ctx, ret: Ret, who: User_) -> list[DisposalOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            DisposalOut(
                id=e.id,
                document_id=e.document_id,
                title=e.title,
                retention_class=e.retention_class,
                versions=len(e.version_hashes),
                reason=e.reason,
                approver_id=e.approver_id,
                created_at=e.created_at,
            )
            for e in ret.events(session, who)
        ]
