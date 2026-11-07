"""Builds the documents services from the shared ones, for the API and for the worker."""

from __future__ import annotations

from typing import Any

from docuflow.modules.documents.imports import MIB, ImportLimits, ImportService
from docuflow.modules.documents.scanner import build_scanner
from docuflow.modules.documents.service import DocumentService
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.service import RepositoryService


def import_limits(settings: Any) -> ImportLimits:
    """Limits from settings. Anything the settings do not say keeps its default."""
    defaults = ImportLimits()
    return ImportLimits(
        max_archive_bytes=getattr(settings, "max_import_bytes", defaults.max_archive_bytes),
        max_entries=getattr(settings, "import_max_entries", defaults.max_entries),
        max_expanded_bytes=getattr(
            settings, "import_max_expanded_mb", defaults.max_expanded_bytes // MIB
        )
        * MIB,
        max_ratio=defaults.max_ratio,
    )


def build_documents(
    services: Any, repo: RepositoryService, policy: PolicyService
) -> tuple[DocumentService, ImportService]:
    docs = DocumentService(
        services.audit,
        policy,
        repo,
        services.objects,
        build_scanner(services.settings),
        services.settings.max_upload_bytes,
    )
    imports = ImportService(
        services.audit, repo, docs, services.objects, import_limits(services.settings)
    )
    return docs, imports
