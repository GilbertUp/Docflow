"""Wires the API: routers, one JSON error shape, and the audit of denied requests."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from docuflow.modules.admin.api import router as admin_router
from docuflow.modules.admin.service import AdminService
from docuflow.modules.assistant.api import router as assistant_router
from docuflow.modules.assistant.providers import build_provider
from docuflow.modules.assistant.service import AssistantService
from docuflow.modules.audit.api import router as audit_router
from docuflow.modules.audit.service import AuditQueryService
from docuflow.modules.collaboration.api import router as collaboration_router
from docuflow.modules.collaboration.service import CollaborationService
from docuflow.modules.compression.api import router as compression_router
from docuflow.modules.compression.service import CompressionService
from docuflow.modules.documents.api import router as documents_router
from docuflow.modules.documents.relationships import RelationshipService
from docuflow.modules.documents.wiring import build_documents
from docuflow.modules.forms.api import router as forms_router
from docuflow.modules.forms.service import FormService
from docuflow.modules.identity.api import (
    MUST_CHANGE_CODE,
    ApiContext,
    build_context,
    router,
)
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.security import RedisRateLimiter
from docuflow.modules.indexing.api import router as indexing_router
from docuflow.modules.indexing.service import IndexingService
from docuflow.modules.indexing.suggest import SuggestionService
from docuflow.modules.integrations.api import router as integrations_router
from docuflow.modules.integrations.idempotency import IdempotencyMiddleware
from docuflow.modules.integrations.service import WebhookService
from docuflow.modules.notifications.api import router as notifications_router
from docuflow.modules.notifications.service import NotificationService
from docuflow.modules.repository.api import router as repository_router
from docuflow.modules.repository.service import RepositoryService
from docuflow.modules.retention.api import router as retention_router
from docuflow.modules.retention.service import RetentionService
from docuflow.modules.search.api import router as search_router
from docuflow.modules.search.service import SearchService
from docuflow.modules.signatures.api import router as signatures_router
from docuflow.modules.signatures.service import SignatureService
from docuflow.modules.workflow.api import router as workflow_router
from docuflow.modules.workflow.service import WorkflowService
from docuflow.platform import context
from docuflow.platform.audit import AuditActor
from docuflow.platform.bootstrap import Services
from docuflow.platform.errors import DomainError, Forbidden, RateLimited, ValidationFailed

log = structlog.get_logger("docuflow.web")
MAX_PATH_IN_OBJECT_ID = 64


def _body(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, **extra},
        "correlation_id": context.correlation_id.get(),
    }


def _record_denial(ctx: ApiContext, principal: Principal, request: Request, exc: Forbidden) -> None:
    """Runs in its own transaction. The failed request's transaction rolled back already."""
    with ctx.services.db.tenant_session(principal.tenant_id) as session:
        ctx.services.audit.record(
            session,
            principal.tenant_id,
            "access.denied",
            actor=AuditActor(
                principal.user_id,
                "user" if principal.kind == "session" else "api_key",
                request.client.host if request.client else None,
                request.headers.get("user-agent"),
            ),
            object_type="route",
            object_id=request.url.path[:MAX_PATH_IN_OBJECT_ID],
            result="failure",
            details={
                "method": request.method,
                "path": request.url.path,
                "reason": exc.code,
                **exc.details,
            },
        )


def install(app: FastAPI, services: Services) -> ApiContext:
    ctx = build_context(services, RedisRateLimiter(services.redis))
    app.state.api = ctx
    app.state.repo = RepositoryService(services.audit, ctx.policy)
    built = build_documents(services, app.state.repo, ctx.policy)
    app.state.documents, app.state.imports, app.state.text = built.docs, built.imports, built.text
    app.state.preview = built.preview
    app.state.relationships = RelationshipService(services.audit, ctx.policy, app.state.repo)
    app.state.indexing = IndexingService(services.audit, ctx.policy, app.state.repo)
    app.state.suggestions = SuggestionService(services.audit, ctx.policy, app.state.repo)
    app.state.search = SearchService(services.audit, ctx.policy)
    app.state.collaboration = CollaborationService(services.audit, ctx.policy, app.state.repo)
    app.state.workflow = WorkflowService(services.audit, ctx.policy, app.state.repo)
    app.state.forms = FormService(services.audit, ctx.policy, app.state.repo)
    app.state.webhooks = WebhookService(
        services.audit,
        ctx.policy,
        services.keys,
        allow_insecure=getattr(services.settings, "webhooks_allow_insecure", False),
    )
    # Every recorded event is offered to the webhooks that asked for it.
    services.audit.add_listener(app.state.webhooks.on_event, "webhooks")
    app.add_middleware(IdempotencyMiddleware, db=services.db)
    app.state.admin = AdminService(services.audit, ctx.policy)
    app.state.assistant = AssistantService(
        services.audit, ctx.policy, app.state.repo, build_provider(services.settings)
    )
    app.state.compression = CompressionService(
        services.audit, ctx.policy, app.state.repo, services.objects
    )
    app.state.audit_query = AuditQueryService(services.audit, ctx.policy, app.state.repo)
    app.state.retention = RetentionService(
        services.audit, ctx.policy, app.state.repo, services.objects
    )
    app.state.signatures = SignatureService(services.audit, ctx.policy, app.state.repo)
    app.state.notification_settings = NotificationService(services.audit, ctx.policy)
    app.include_router(router)
    app.include_router(repository_router)
    app.include_router(documents_router)
    app.include_router(indexing_router)
    app.include_router(search_router)
    app.include_router(collaboration_router)
    app.include_router(workflow_router)
    app.include_router(forms_router)
    app.include_router(integrations_router)
    app.include_router(admin_router)
    app.include_router(assistant_router)
    app.include_router(compression_router)
    app.include_router(audit_router)
    app.include_router(retention_router)
    app.include_router(signatures_router)
    app.include_router(notifications_router)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        principal: Principal | None = getattr(request.state, "principal", None)
        if isinstance(exc, Forbidden) and principal is not None and exc.code != MUST_CHANGE_CODE:
            try:
                await run_in_threadpool(_record_denial, ctx, principal, request, exc)
            except Exception:
                # The caller still gets the refusal, and the failure is visible to operators.
                log.exception("denial_audit_failed")
        headers = {"Retry-After": "60"} if isinstance(exc, RateLimited) else None
        extra = {"fields": exc.fields} if isinstance(exc, ValidationFailed) else {}
        return JSONResponse(
            status_code=exc.status,
            content=_body(exc.code, exc.message, **extra),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field names and messages only: the submitted values may be passwords.
        fields = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_body("validation_error", "The request is not valid.", fields=fields),
        )

    return ctx
