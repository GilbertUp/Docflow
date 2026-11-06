"""Request-scoped context shared by logging, audit and jobs."""

from __future__ import annotations

from contextvars import ContextVar

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
