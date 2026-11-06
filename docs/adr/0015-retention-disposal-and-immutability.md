# ADR 0015: Retention, legal hold and destruction rules

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
The documents require immutable originals, retention, legal hold, reviewed disposal and privacy deletion. These pull against each other unless precedence is defined.

## Decision

### Meaning of immutable
Bytes are never modified or replaced. Destruction is a separate, authorised, recorded act.

### Precedence, highest first
1. **Legal hold** blocks disposal and privacy erasure while active. Erasure requests are then recorded as blocked, with the reason.
2. **Retention period** blocks ordinary deletion until it ends, unless a lawful ground recorded by an authorised officer applies.
3. **Disposal review** is always required. The system never deletes on a timer.
4. **User deletion** is a recoverable soft delete only and is subject to rules 1 to 3.

### Disposal flow
Eligibility is calculated by a scheduled job and only marks records. An authorised reviewer approves through a workflow. Approval re-checks holds in the same transaction as the destruction order, closing the gap between review and action.

### Destruction
1. Destroy the document's data keys (crypto-shredding), making any backup copies unreadable too.
2. Delete objects, derivatives, OCR text, chunks and embeddings. Objects shared through deduplication are retained until unreferenced, and their per-version access is removed.
3. Keep a `disposal_event` containing identifiers, hash, policy, approver and timestamp, but no content. It lasts beyond the document.

### Calendar rules
Dates are calculated in the tenant's time zone and stored in UTC. Trigger events can be created date, a metadata field such as contract end, or a workflow event.

## Consequences
- Disposal works alongside tamper-evident audit: audit events persist while containing no document content.
- Key management becomes part of records management, so key backups need the same discipline.
- Per-object keys under deduplication mean a shared object has one key. Shredding therefore waits until the last reference is removed.
- These rules are configurable controls. They do not by themselves establish legal compliance.
