# ADR 0031: Reading, exporting and verifying the audit trail

**Status:** Accepted on 2026-11-08 (F16, first slice)

## Context
The trail is written and chained since M0 ([ADR 0012](0012-tamper-evident-audit.md)), but nobody could read it. People who answer for the system need to find what happened, hand a copy to someone else, and prove the copy is genuine. Doing so must not open a new hole: the trail records what people did, and the people reading it are themselves a risk.

## Decision
- **One right.** Everything here needs the system right `audit.read`, which already existed in the catalogue. Being able to see a document does not let you read its trail.
- **Search.** `GET /audit/events` filters by actor, exact action or action prefix, object type and id, result, and a time range. Results are newest first, at most 200 a page, with a `before_seq` cursor. A prefix is matched literally, so `%` and `_` are not patterns. A period whose end is before its start is refused.
- **Document timeline.** `GET /documents/{id}/audit` lists everything recorded about one document. It needs `audit.read` and also the right to read the document, so a document the person cannot see is "not found". This is the timeline that F05 and F10 pointed to.
- **Export.** `GET /audit/export` streams CSV, oldest first, with each event's hash and the hash before it, and returns the head of the chain (`X-Chain-Head-Seq`, `X-Chain-Head-Hash`) as it was when the export was made. At most 50,000 events, flagged with `X-Export-Truncated` when there are more. A cell that starts with `=`, `+`, `-`, `@` or a tab is prefixed with a quote so a spreadsheet will not run it. The response is `private, no-store`.
- **Verify.** `POST /audit/verify` walks every event's link and hash, then compares the chain with the checkpoints held in object storage, and reports the first bad event or a checkpoint mismatch separately.
- **The readers are on the trail.** Exports and verifications are themselves events (`audit.exported` with the filters used and the number of events, and `audit.verified` with the result). Searching is not recorded, to keep the trail useful, as ADR 0012 allows for read-heavy actions.
- **Ids that order.** Timeline and history views order by time, then by id. On a machine with a coarse clock many events share a timestamp, and the random tail of the old id put them in random order. `uuid7` now increases within a process, including inside one millisecond and when the clock steps back. This also fixes the order of workflow history and any other list sorted this way.
- **Web.** An Audit trail page (filters, older events, CSV export, a check button) and a History dialog on each document for people with the right.

## Not in this slice
- Scheduled verification and alerts when it fails, and a report of failed verifications.
- Policy for recording reads and searches per organization, and retention of the trail.
- Sealed, signed exports and a tool to check an export against a live trail offline.
- Compliance reports (who accessed what, access reviews, privileged-action summaries) and export of a legal-hold or disposal evidence pack.
- Filtering by free text inside the details, and by several values at once.

## Consequences
- Exports and verification read the whole trail, so on a large organization they are slow. They are bounded, and moving verification to a scheduled job is the next step.
- The new id rule is per process. Two processes can still make ids in the same millisecond, so code must keep ordering by time first.
