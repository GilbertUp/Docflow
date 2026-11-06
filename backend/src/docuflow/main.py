"""API application factory. Business routers join under /api/v1 as features land."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from docuflow import __version__
from docuflow.platform.bootstrap import Services, get_services
from docuflow.platform.observability import RequestContextMiddleware, configure_logging

log = structlog.get_logger("docuflow.api")


def _check(name: str, probe: Any, results: dict[str, str]) -> None:
    try:
        probe()
    except Exception:  # readiness reports failure and must not raise
        log.warning("readiness_failed", dependency=name, exc_info=True)
        results[name] = "unavailable"
    else:
        results[name] = "ok"


def readiness(services: Services) -> dict[str, str]:
    def database() -> None:
        with services.db.unscoped_session() as session:
            session.execute(text("SELECT 1"))

    results: dict[str, str] = {}
    _check("database", database, results)
    _check("queue", services.redis.ping, results)
    _check("storage", services.store.check, results)
    return results


def create_app(services: Services | None = None) -> FastAPI:
    active = services or get_services()
    configure_logging(active.settings.log_level)
    app = FastAPI(
        title="DocuFlow API",
        version=__version__,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )
    app.add_middleware(RequestContextMiddleware)

    health = APIRouter(tags=["health"])

    @health.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @health.get("/health/ready")
    def ready() -> JSONResponse:
        checks = readiness(active)
        healthy = all(value == "ok" for value in checks.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "degraded", "checks": checks},
        )

    @health.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(health)
    return app


def app_factory() -> FastAPI:
    return create_app()
