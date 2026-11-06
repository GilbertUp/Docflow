# ADR 0012: Hash-chained audit with external checkpoints

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
The audit trail must detect modification. A plain hash chain inside the database does not stop someone with full database access from rewriting the entire chain.

## Decision
- Audit events are written in the same transaction as the action they record, so a recorded action cannot lose its event.
- `docuflow_app` holds only `INSERT` and `SELECT` on `audit_event`. A trigger also rejects updates and deletes.
- Each event stores a per-tenant sequence number and an HMAC over its canonical content plus the previous event's hash. The HMAC key is held outside the database.
- A periodic checkpoint writes the chain head and sequence to object-locked storage, outside the database's trust boundary.
- A verification job walks the chain and compares checkpoints, and reports the first broken event.
- Fields are redaction-checked before writing: no passwords, tokens or document content.
- Read and search events are recorded according to a tenant policy, because they are high-volume.

## Consequences
- Per-tenant sequencing serialises audit writes within a tenant using a short advisory lock. Throughput is measured in load tests. If it limits the system, batching is the fallback, which can be introduced without altering verification.
- Backup and restore must preserve chain continuity, and restores are recorded as chain events.
- Time-based partitioning keeps the table manageable.
