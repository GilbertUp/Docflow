"""Wires the API: routers, one JSON error shape, and the audit of denied requests."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from docuflow.modules.documents.api import router as documents_router
from docuflow.modules.documents.service import DocumentService
from docuflow.modules.identity.api import (
    MUST_CHANGE_CODE,
    ApiContext,
    build_context,
    router,
)
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.security import RedisRateLimiter
from docuflow.modules.repository.api import router as repository_router
from docuflow.modules.repository.service import RepositoryService
from docuflow.platform import context
from docuflow.platform.audit import AuditActor
from docuflow.platform.bootstrap import Services
from docuflow.platform.errors import DomainError, Forbidden, RateLimited

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
    app.state.documents = DocumentService(
        services.audit,
        ctx.policy,
        app.state.repo,
        services.objects,
        services.settings.max_upload_bytes,
    )
    app.include_router(router)
    app.include_router(repository_router)
    app.include_router(documents_router)

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
        return JSONResponse(
            status_code=exc.status, content=_body(exc.code, exc.message), headers=headers
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
