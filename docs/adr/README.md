# Architecture decision records

All records were **accepted** on 2026-11-06, when the owner accepted the plan defaults. A decision that changes later gets a new record that supersedes the old one.

| ADR | Decision | Owner choice recorded |
|---|---|---|
| [0001](0001-modular-monolith.md) | Modular monolith with enforced boundaries | None needed |
| [0002](0002-sync-services-shared-by-api-and-workers.md) | Synchronous SQLAlchemy, services shared by API and workers | None needed |
| [0003](0003-tenant-isolation.md) | Tenant isolation through PostgreSQL row-level security | None needed |
| [0004](0004-authorization-model.md) | ACL inheritance resolved through anchors | Type entries only restrict |
| [0005](0005-authentication-and-sessions.md) | Server-side sessions; API keys for machines | None needed |
| [0006](0006-object-storage-and-immutability.md) | S3 adapter, SeaweedFS candidate, envelope encryption | SeaweedFS, subject to the M0 spike |
| [0007](0007-background-jobs.md) | PostgreSQL job state, outbox, Celery execution | None needed |
| [0008](0008-document-processing-and-viewer.md) | PDF libraries, sandboxed conversion, image-based viewer | Closed-source; legal confirmation still advised |
| [0009](0009-intelligent-indexing.md) | Layered extraction with mandatory evidence | None needed |
| [0010](0010-search.md) | Hybrid PostgreSQL search with permissions in the query | None needed |
| [0011](0011-workflow-engine.md) | Custom token-based workflow engine | None needed |
| [0012](0012-tamper-evident-audit.md) | Hash-chained audit with external checkpoints | None needed |
| [0013](0013-ai-assistant-and-providers.md) | Provider adapters, RAG, extract-only fallback | External AI off by default |
| [0014](0014-compression-engine.md) | Page-level compression with evaluated derivatives | None needed |
| [0015](0015-retention-disposal-and-immutability.md) | Retention, legal hold and destruction rules | None needed |
| [0016](0016-folder-hierarchy-and-anchors.md) | Folder hierarchy mirrored by the permission anchors | None needed |
| [0017](0017-upload-pipeline.md) | Upload pipeline: scan, store, version | None needed |
| [0018](0018-archive-import.md) | ZIP import as a background job | None needed |
| [0019](0019-text-extraction-and-languages.md) | Text extraction and OCR languages | None needed |
| [0020](0020-document-types-and-index-fields.md) | Document types with versioned fields | None needed |
| [0021](0021-intelligent-indexing.md) | Rule-based type and value suggestions with sources | None needed |
| [0022](0022-page-preview.md) | Server-rendered page images, never the file | None needed |
| [0023](0023-search.md) | Permission-aware keyword search | None needed |
| [0024](0024-restore-and-relationships.md) | Restore as a new version, typed links | None needed |
| [0025](0025-comments-tasks-notifications.md) | Comments, tasks and in-app notifications | None needed |
| [0026](0026-workflows.md) | Ordered approval and task workflows | None needed |
| [0027](0027-forms.md) | Versioned forms, checked submissions | None needed |
| [0028](0028-notifications-and-deadlines.md) | Email delivery tracking, task deadlines | None needed |
| [0029](0029-approvals-signatures-stamps.md) | Version-bound approvals, internal signatures, stamps | External e-signature provider needs owner account |
| [0030](0030-retention-holds-disposal.md) | Retention classes, legal holds, disposal | None needed |
