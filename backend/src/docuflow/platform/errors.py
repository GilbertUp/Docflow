"""Domain errors. The API turns these into one consistent JSON shape."""

from __future__ import annotations


class DomainError(Exception):
    status = 400
    code = "invalid_request"

    def __init__(
        self, message: str, code: str | None = None, details: dict[str, object] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code


class Unauthenticated(DomainError):
    status = 401
    code = "unauthenticated"


class Forbidden(DomainError):
    status = 403
    code = "forbidden"


class NotFound(DomainError):
    """Also used when a user may not see a resource, so its existence is not revealed."""

    status = 404
    code = "not_found"


class Conflict(DomainError):
    status = 409
    code = "conflict"


class RateLimited(DomainError):
    status = 429
    code = "rate_limited"


class ValidationFailed(DomainError):
    """Values that did not pass, with a message per field so a form can show each one."""

    status = 422
    code = "validation_error"

    def __init__(self, message: str, fields: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.fields = fields
