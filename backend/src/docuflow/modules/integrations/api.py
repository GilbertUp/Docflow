"""Webhooks over HTTP. They need the integrations.manage right."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from docuflow.modules.identity.api import Ctx, User_
from docuflow.modules.integrations.models import WebhookDelivery, WebhookSubscription
from docuflow.modules.integrations.service import Created, WebhookService
from docuflow.modules.integrations.webhooks import EVENTS

router = APIRouter(prefix="/api/v1", tags=["integrations"])


def get_webhooks(request: Request) -> WebhookService:
    service: WebhookService = request.app.state.webhooks
    return service


Hooks = Annotated[WebhookService, Depends(get_webhooks)]


class WebhookOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    events: list[str]
    status: str
    created_at: datetime


class WebhookCreated(WebhookOut):
    # Shown this once. Keep it to check the signature on each call. It cannot be read again.
    secret: str


class CreateWebhookRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=500)
    events: list[str] = Field(min_length=1, max_length=50)


class UpdateWebhookRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=500)
    events: list[str] | None = Field(default=None, max_length=50)
    status: Literal["active", "disabled"] | None = None


class WebhookDeliveryOut(BaseModel):
    id: uuid.UUID
    event_type: str
    status: str
    attempts: int
    response_status: int | None
    error: str | None
    created_at: datetime
    delivered_at: datetime | None
    redelivery_of: uuid.UUID | None


class WebhookEventTypeOut(BaseModel):
    type: str
    description: str


def _hook(row: WebhookSubscription) -> WebhookOut:
    return WebhookOut(
        id=row.id,
        name=row.name,
        url=row.url,
        events=list(row.events),
        status=row.status,
        created_at=row.created_at,
    )


def _created(made: Created) -> WebhookCreated:
    return WebhookCreated(**_hook(made.subscription).model_dump(), secret=made.secret)


def _delivery(row: WebhookDelivery) -> WebhookDeliveryOut:
    return WebhookDeliveryOut(
        id=row.id,
        event_type=row.event_type,
        status=row.status,
        attempts=row.attempts,
        response_status=row.response_status,
        error=row.last_error,
        created_at=row.created_at,
        delivered_at=row.delivered_at,
        redelivery_of=row.redelivery_of,
    )


@router.get("/webhook-events")
def event_types(who: User_) -> list[WebhookEventTypeOut]:
    """The events that can be subscribed to."""
    return [WebhookEventTypeOut(type=t, description=d) for t, d in EVENTS.items()]


@router.get("/webhooks")
def list_webhooks(ctx: Ctx, hooks: Hooks, who: User_) -> list[WebhookOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_hook(r) for r in hooks.subscriptions(session, who)]


@router.post("/webhooks", status_code=201)
def create_webhook(
    body: CreateWebhookRequest, ctx: Ctx, hooks: Hooks, who: User_
) -> WebhookCreated:
    """Subscribes an address. The signing secret in the answer is shown only now."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _created(hooks.create(session, who, body.name, body.url, body.events))


@router.get("/webhooks/{webhook_id}")
def get_webhook(webhook_id: uuid.UUID, ctx: Ctx, hooks: Hooks, who: User_) -> WebhookOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _hook(hooks.get(session, who, webhook_id))


@router.patch("/webhooks/{webhook_id}")
def update_webhook(
    webhook_id: uuid.UUID, body: UpdateWebhookRequest, ctx: Ctx, hooks: Hooks, who: User_
) -> WebhookOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _hook(
            hooks.update(
                session,
                who,
                webhook_id,
                name=body.name,
                url=body.url,
                events=body.events,
                status=body.status,
            )
        )


@router.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(webhook_id: uuid.UUID, ctx: Ctx, hooks: Hooks, who: User_) -> Response:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        hooks.delete(session, who, webhook_id)
    return Response(status_code=204)


@router.post("/webhooks/{webhook_id}/secret")
def rotate_secret(webhook_id: uuid.UUID, ctx: Ctx, hooks: Hooks, who: User_) -> WebhookCreated:
    """Replaces the signing secret. The old one stops working at once."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _created(hooks.rotate_secret(session, who, webhook_id))


@router.post("/webhooks/{webhook_id}/test", status_code=202)
def test_webhook(webhook_id: uuid.UUID, ctx: Ctx, hooks: Hooks, who: User_) -> WebhookDeliveryOut:
    """Queues a ping, to check the address and the signature."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _delivery(hooks.test(session, who, webhook_id))


@router.get("/webhooks/{webhook_id}/deliveries")
def list_deliveries(
    webhook_id: uuid.UUID, ctx: Ctx, hooks: Hooks, who: User_
) -> list[WebhookDeliveryOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_delivery(r) for r in hooks.deliveries(session, who, webhook_id)]


@router.post("/webhooks/{webhook_id}/deliveries/{delivery_id}/redeliver", status_code=202)
def redeliver(
    webhook_id: uuid.UUID, delivery_id: uuid.UUID, ctx: Ctx, hooks: Hooks, who: User_
) -> WebhookDeliveryOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _delivery(hooks.redeliver(session, who, webhook_id, delivery_id))
