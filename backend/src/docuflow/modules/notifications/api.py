"""Notification preferences, deadlines and email delivery over HTTP."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from docuflow.modules.identity.api import Ctx, User_
from docuflow.modules.notifications.service import NotificationService, SlaView

router = APIRouter(prefix="/api/v1", tags=["notifications"])


def get_notifications(request: Request) -> NotificationService:
    service: NotificationService = request.app.state.notification_settings
    return service


Notes = Annotated[NotificationService, Depends(get_notifications)]


class PreferenceOut(BaseModel):
    kind: str
    description: str
    email: bool


class SetPreferencesRequest(BaseModel):
    # Kind of notification to whether it should also be emailed. Kinds left out are unchanged.
    email: dict[str, bool]


class SlaOut(BaseModel):
    reminder_before_hours: int
    escalate_after_hours: int | None
    escalate_to_user_id: uuid.UUID | None
    escalate_to_name: str | None


class SetSlaRequest(BaseModel):
    reminder_before_hours: int = Field(ge=0, le=24 * 365)
    escalate_after_hours: int | None = Field(default=None, ge=0, le=24 * 365)
    escalate_to_user_id: uuid.UUID | None = None


class DeliveryOut(BaseModel):
    id: uuid.UUID
    subject: str
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None


def _sla(view: SlaView) -> SlaOut:
    return SlaOut(
        reminder_before_hours=view.reminder_before_hours,
        escalate_after_hours=view.escalate_after_hours,
        escalate_to_user_id=view.escalate_to_user_id,
        escalate_to_name=view.escalate_to_name,
    )


@router.get("/notification-preferences")
def get_preferences(ctx: Ctx, notes: Notes, who: User_) -> list[PreferenceOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [PreferenceOut(**vars(p)) for p in notes.preferences(session, who)]


@router.put("/notification-preferences")
def set_preferences(
    body: SetPreferencesRequest, ctx: Ctx, notes: Notes, who: User_
) -> list[PreferenceOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [PreferenceOut(**vars(p)) for p in notes.set_preferences(session, who, body.email)]


@router.get("/email-deliveries")
def deliveries(ctx: Ctx, notes: Notes, who: User_) -> list[DeliveryOut]:
    """The caller's own emails, and what happened when each was sent."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            DeliveryOut(
                id=row.id,
                subject=row.subject,
                status=row.status,
                attempts=row.attempts,
                last_error=row.last_error,
                created_at=row.created_at,
                sent_at=row.sent_at,
            )
            for row in notes.deliveries(session, who)
        ]


@router.get("/sla-policy")
def get_sla(ctx: Ctx, notes: Notes, who: User_) -> SlaOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _sla(notes.sla(session))


@router.put("/sla-policy")
def set_sla(body: SetSlaRequest, ctx: Ctx, notes: Notes, who: User_) -> SlaOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _sla(
            notes.set_sla(
                session,
                who,
                body.reminder_before_hours,
                body.escalate_after_hours,
                body.escalate_to_user_id,
            )
        )
