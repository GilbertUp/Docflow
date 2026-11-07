from __future__ import annotations

import pytest

from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.service import RepositoryService
from docuflow.platform.audit import AuditLog


@pytest.fixture
def policy() -> PolicyService:
    return PolicyService()


@pytest.fixture
def repo(audit: AuditLog, policy: PolicyService) -> RepositoryService:
    return RepositoryService(audit, policy)
