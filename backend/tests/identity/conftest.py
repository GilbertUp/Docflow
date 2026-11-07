from __future__ import annotations

from typing import Any

import pytest

from docuflow.modules.identity.admin import IdentityAdmin
from docuflow.modules.identity.auth import AuthService
from docuflow.modules.identity.policy import PolicyService
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database


class OpenLimiter:
    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        return True


class DenyLimiter:
    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        return False


@pytest.fixture
def auth(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> AuthService:
    return AuthService(db, audit, keys, OpenLimiter())


@pytest.fixture
def policy() -> PolicyService:
    return PolicyService()


@pytest.fixture
def admin(audit: AuditLog, policy: PolicyService, auth: AuthService) -> IdentityAdmin:
    return IdentityAdmin(audit, policy, auth)


@pytest.fixture
def deny_limiter() -> Any:
    return DenyLimiter()
