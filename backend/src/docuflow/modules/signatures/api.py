"""Approvals, signatures and stamps over HTTP."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from docuflow.modules.identity.api import Ctx, User_
from docuflow.modules.signatures.service import (
    ApprovalView,
    SignatureService,
    SignatureView,
)
from docuflow.platform.errors import Forbidden

router = APIRouter(prefix="/api/v1", tags=["signatures"])


def get_signatures(request: Request) -> SignatureService:
    service: SignatureService = request.app.state.signatures
    return service


Sigs = Annotated[SignatureService, Depends(get_signatures)]


class ApprovalOut(BaseModel):
    id: uuid.UUID
    decision: str
    reason: str | None
    decider_id: uuid.UUID
    decider_name: str
    version_number: int
    version_hash: str
    # True only while that version is still the document's current one.
    current: bool
    created_at: datetime


class SignRequest(BaseModel):
    # The person's own password, given again to sign.
    password: str = Field(min_length=1, max_length=200)
    statement: str = Field(default="", max_length=500)


class RevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class SignatureOut(BaseModel):
    id: uuid.UUID
    kind: str
    statement: str
    status: str
    signer_id: uuid.UUID
    signer_name: str
    version_number: int
    bound_hash: str
    # True while the signed version is the document's current one and the signature stands.
    current: bool
    # True when the stored file still has the hash that was signed.
    intact: bool
    created_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None


class StampOut(BaseModel):
    id: uuid.UUID
    name: str
    text: str
    color: str
    status: str


class CreateStampRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=30)
    color: Literal["red", "green", "blue", "black"] = "red"


class SetStampStatusRequest(BaseModel):
    status: Literal["active", "archived"]


class ApplyStampRequest(BaseModel):
    stamp_id: uuid.UUID
    page: int = Field(ge=1, le=500)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class AppliedStampOut(BaseModel):
    id: uuid.UUID
    text: str
    color: str
    page: int
    x: float
    y: float
    applied_by: str
    current: bool
    created_at: datetime


def _approval(view: ApprovalView) -> ApprovalOut:
    row = view.approval
    return ApprovalOut(
        id=row.id,
        decision=row.decision,
        reason=row.reason,
        decider_id=row.decider_id,
        decider_name=view.decider_name,
        version_number=view.version_number,
        version_hash=row.version_hash,
        current=view.current,
        created_at=row.created_at,
    )


def _signature(view: SignatureView) -> SignatureOut:
    row = view.signature
    return SignatureOut(
        id=row.id,
        kind=row.kind,
        statement=row.statement,
        status=row.status,
        signer_id=row.signer_id,
        signer_name=view.signer_name,
        version_number=view.version_number,
        bound_hash=row.bound_hash,
        current=view.current,
        intact=view.intact,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        revoked_reason=row.revoked_reason,
    )


@router.get("/documents/{document_id}/approvals")
def list_approvals(document_id: uuid.UUID, ctx: Ctx, sigs: Sigs, who: User_) -> list[ApprovalOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_approval(v) for v in sigs.approvals(session, who, document_id)]


@router.get("/documents/{document_id}/signatures")
def list_signatures(document_id: uuid.UUID, ctx: Ctx, sigs: Sigs, who: User_) -> list[SignatureOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_signature(v) for v in sigs.signatures(session, who, document_id)]


@router.post("/documents/{document_id}/signatures", status_code=201)
def sign_document(
    document_id: uuid.UUID, body: SignRequest, ctx: Ctx, sigs: Sigs, who: User_
) -> SignatureOut:
    """Signs the current version as an internal attestation. Needs the password again."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        signed = sigs.sign(session, who, document_id, body.password, body.statement)
    # The session above is saved first, so a wrong password still counts toward the lockout.
    if signed is None:
        raise Forbidden("That password is not right.", code="bad_password")
    return _signature(signed)


@router.post("/signatures/{signature_id}/revoke")
def revoke_signature(
    signature_id: uuid.UUID, body: RevokeRequest, ctx: Ctx, sigs: Sigs, who: User_
) -> SignatureOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _signature(sigs.revoke(session, who, signature_id, body.reason))


@router.get("/stamps")
def list_stamps(ctx: Ctx, sigs: Sigs, who: User_, include_archived: bool = False) -> list[StampOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            StampOut(id=s.id, name=s.name, text=s.text, color=s.color, status=s.status)
            for s in sigs.stamps(session, include_archived)
        ]


@router.post("/stamps", status_code=201)
def create_stamp(body: CreateStampRequest, ctx: Ctx, sigs: Sigs, who: User_) -> StampOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        s = sigs.create_stamp(session, who, body.name, body.text, body.color)
        return StampOut(id=s.id, name=s.name, text=s.text, color=s.color, status=s.status)


@router.patch("/stamps/{stamp_id}")
def update_stamp(
    stamp_id: uuid.UUID, body: SetStampStatusRequest, ctx: Ctx, sigs: Sigs, who: User_
) -> StampOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        s = sigs.set_stamp_status(session, who, stamp_id, body.status)
        return StampOut(id=s.id, name=s.name, text=s.text, color=s.color, status=s.status)


@router.get("/documents/{document_id}/stamps")
def document_stamps(
    document_id: uuid.UUID, ctx: Ctx, sigs: Sigs, who: User_
) -> list[AppliedStampOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            AppliedStampOut(
                id=row.id,
                text=row.text,
                color=row.color,
                page=row.page,
                x=row.x,
                y=row.y,
                applied_by=name,
                current=current,
                created_at=row.created_at,
            )
            for row, name, current in sigs.applied(session, who, document_id)
        ]


@router.post("/documents/{document_id}/stamps", status_code=201)
def apply_stamp(
    document_id: uuid.UUID, body: ApplyStampRequest, ctx: Ctx, sigs: Sigs, who: User_
) -> AppliedStampOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        row = sigs.apply_stamp(session, who, document_id, body.stamp_id, body.page, body.x, body.y)
        return AppliedStampOut(
            id=row.id,
            text=row.text,
            color=row.color,
            page=row.page,
            x=row.x,
            y=row.y,
            applied_by="You",
            current=True,
            created_at=row.created_at,
        )


@router.delete("/documents/{document_id}/stamps/{application_id}", status_code=204)
def remove_stamp(
    document_id: uuid.UUID, application_id: uuid.UUID, ctx: Ctx, sigs: Sigs, who: User_
) -> Response:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        sigs.remove_stamp(session, who, document_id, application_id)
    return Response(status_code=204)
