"""Password hashing, token format and rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from typing import Protocol

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from redis import Redis
from redis.exceptions import RedisError

from docuflow.platform.errors import DomainError

MIN_PASSWORD_LENGTH = 12
COMMON_PASSWORDS = frozenset(
    {"password1234", "123456789012", "qwertyuiop12", "administrator", "welcome12345"}
)
log = structlog.get_logger("docuflow.identity")

# argon2-cffi defaults follow RFC 9106 and use the Argon2id variant.
_hasher = PasswordHasher()
# Verified when the account does not exist, so response time does not reveal valid emails.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


def validate_password(password: str, email: str) -> None:
    lowered = password.lower()
    if len(password) < MIN_PASSWORD_LENGTH:
        raise DomainError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", "weak_password"
        )
    if lowered in COMMON_PASSWORDS:
        raise DomainError("That password is too common.", "weak_password")
    local_part = email.split("@", maxsplit=1)[0].lower()
    if len(local_part) >= 4 and local_part in lowered:
        raise DomainError("Password must not contain your email name.", "weak_password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def make_token(tenant_id: uuid.UUID, prefix: str = "") -> str:
    """The tenant is readable from the token, so lookups never need a cross-tenant query."""
    return f"{prefix}{tenant_id.hex}.{secrets.token_urlsafe(32)}"


def parse_tenant(token: str, prefix: str = "") -> uuid.UUID | None:
    if not token.startswith(prefix):
        return None
    head = token[len(prefix) :].split(".", maxsplit=1)[0]
    try:
        return uuid.UUID(hex=head)
    except ValueError:
        return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(session_token: str) -> str:
    """Derived from the secret session token, so it needs no storage and cannot be guessed."""
    return hmac.new(session_token.encode(), b"csrf", hashlib.sha256).hexdigest()


class RateLimiter(Protocol):
    def allow(self, key: str, limit: int, window_seconds: int) -> bool: ...


class RedisRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        name = f"docuflow:rate:{key}"
        try:
            count = self._redis.incr(name)
            if count == 1:
                self._redis.expire(name, window_seconds)
        except RedisError:
            # Account lockout in PostgreSQL still applies, so login survives a Redis outage.
            log.warning("rate_limiter_unavailable")
            return True
        return int(count) <= limit  # type: ignore[arg-type]
