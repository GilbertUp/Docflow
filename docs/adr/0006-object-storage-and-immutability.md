# ADR 0006: S3 adapter, SeaweedFS candidate, envelope encryption

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
The guide names MinIO for local use. Secondary sources report that MinIO stopped publishing community images in October 2025, entered maintenance mode in December 2025, and archived the repository in 2026. Dates differ between sources. Either way, an unmaintained storage service should not hold the primary records of a production DMS.

## Decision

### Backend
All code uses an `ObjectStore` interface over the S3 API. No backend-specific code enters domain modules.

| Option | Licence (reported) | Assessment |
|---|---|---|
| **SeaweedFS** | Apache 2.0 | Candidate. Reported to support versioning and object lock. |
| Garage | AGPL v3 | Lightweight, but reported to lack bucket versioning, which object lock needs. |
| Community MinIO fork | AGPL v3 | Fallback; depends on a small maintainer group. |
| Cloud S3 or equivalent | Commercial | Production option, subject to data-location rules. |

**M0 spike:** an automated conformance suite covers multipart upload, range reads, versioning and object lock, checks reported claims first-hand, and measures memory. The same suite qualifies any future backend.

### Immutability
- Original bytes are hashed with SHA-256 during streaming, and the hash is recorded before the job continues.
- Objects are never overwritten. New content always creates a new object.
- User filenames are metadata only and never appear in storage keys.
- Object keys use an HMAC of tenant and content hash, so bucket listings reveal neither.

### Encryption
Streaming, chunked AES-256-GCM with a per-object data key wrapped by a tenant key. The `KeyProvider` interface supports environment-supplied keys for development, and Vault or a cloud KMS for production. Chunking allows range reads.

### Deduplication
Per tenant only, because cross-tenant deduplication would reveal that another tenant holds a file. A duplicate upload creates a flagged link rather than a second object.

## Spike result (2026-11-07)

SeaweedFS 4.22 passed the conformance suite in CI: versioning, object lock on a compliance-mode version, multipart upload, range reads, and the encrypted storage suite. It used 176 MiB in that run. Each bucket needs its own volume slots, so `-volume.max` must allow for that. MinIO's reported status was not checked first-hand, because the choice no longer depends on it.

## Consequences
- Storage can change without touching domain code.
- Application-level encryption makes the backend untrusted, and keys become critical: backups must include wrapped keys, and key recovery goes into the DR runbook.
