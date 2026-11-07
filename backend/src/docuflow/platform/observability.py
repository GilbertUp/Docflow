"""Structured logs, correlation IDs, metrics and security headers (F22 foundations)."""

from __future__ import annotations

import logging
import re
import sys
import time

import structlog
from prometheus_client import Counter, Histogram
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from docuflow.platform import context
from docuflow.platform.ids import uuid7

REQUESTS = Counter("docuflow_http_requests_total", "HTTP requests", ["method", "status"])
LATENCY = Histogram("docuflow_http_request_seconds", "HTTP latency", ["method"])

# Client values reach logs and audit, so anything unexpected is replaced rather than trusted.
SAFE_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
}


def _add_correlation_id(
    _logger: object, _method: str, event: structlog.types.EventDict
) -> structlog.types.EventDict:
    correlation = context.correlation_id.get()
    if correlation:
        event["correlation_id"] = correlation
    return event


def configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_correlation_id,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        cache_logger_on_first_use=True,
    )


class RequestContextMiddleware:
    """Pure ASGI middleware, so large streaming uploads and downloads are not buffered."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._log = structlog.get_logger("docuflow.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        supplied = Headers(scope=scope).get("x-correlation-id", "")
        correlation = supplied if SAFE_CORRELATION_ID.match(supplied) else uuid7().hex
        token = context.correlation_id.set(correlation)
        method = scope["method"]
        status = 500
        response_started = False
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Correlation-ID"] = correlation
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if scope["path"].startswith("/api/"):
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        except Exception as exc:
            # Handled here, not by a FastAPI handler, because those run outside this middleware
            # and would lose the correlation ID and security headers.
            self._log.error("unhandled_error", exc_info=exc)
            if response_started:
                raise  # Headers are already sent, so the connection must be aborted.
            body = JSONResponse(
                status_code=500,
                content={
                    "error": {"code": "internal_error", "message": "Something went wrong."},
                    "correlation_id": correlation,
                },
            )
            await body(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            REQUESTS.labels(method=method, status=str(status)).inc()
            LATENCY.labels(method=method).observe(elapsed)
            self._log.info(
                "request",
                method=method,
                path=scope["path"],
                status=status,
                duration_ms=round(elapsed * 1000, 1),
            )
            context.correlation_id.reset(token)
