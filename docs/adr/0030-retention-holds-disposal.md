# ADR 0030: Retention, legal holds and disposal

**Status:** Accepted on 2026-11-08 (F15, first slice)

## Context
Until now a document could never be destroyed. Organizations must keep some documents for a set time, must stop destruction while a dispute or audit is open, and must be able to prove that what was destroyed was destroyed on purpose. "Originals are immutable" (from the implementation plan) means never changed. It does not mean never destroyed after proper authority.

This implements the first part of [ADR 0015](0015-retention-disposal-and-immutability.md). Its precedence holds here: a legal hold wins, disposal always needs a person's approval and never runs on a timer, and the hold is checked again in the same transaction that destroys the document.

## Decision
- **Retention classes.** A class is a name, a number of days to keep, and a clock start: when the document was filed, or when its newest version was added. A document has at most one class, assigned by someone with the new system right `retention.manage` (migration 0017 adds it to existing installs). Eligibility is worked out when asked, from the class and the document's dates, so a new version restarts a "last version" clock and changing a class changes every document under it. Changes are audited with what they replaced.
- **Legal holds.** A hold has a name, a reason and a state. Adding documents to it, taking them off, and releasing it (with a reason) need `retention.manage` and are audited. A document on any active hold cannot be disposed of, however old it is. A hold lists only the documents the caller may read, and says how many there are in all, so a manager cannot learn the titles of documents they cannot see.
- **Disposal is the only way to destroy a document,** and it is refused unless all of these are true: the document has a retention class, its period has ended, no active hold covers it, and the approver has the resource right `dispose` on it. `dispose` is not given to cabinet owners automatically, so approving destruction is a deliberate grant. The reason is optional and recorded.
- **What is destroyed.** The document, every version, pages and recognised text, extracted fields and corrections, typed values, comments and mentions, tasks, workflow runs and their history, approvals, signatures, stamps, links, form submissions, search rows and notifications about it. Children are deleted before parents, because tasks and workflow steps refer to each other and the database cannot delete them in one cascade. A stored file is removed only when no other document's version still uses it, so deduplicated files are kept. Files are removed after the records are saved, and a failure to remove one is logged, never a reason to undo an approved disposal.
- **One permanent record.** `disposal_event` keeps the document's id and title, the class, when it became eligible, who approved, why, and the SHA-256 and size of every file destroyed. It has no foreign keys to anything deleted. The audit trail also records `document.disposed`.
- **Disposal queue.** `GET /disposal/candidates` lists documents past retention that nothing holds and that the caller may read and dispose of.
- **Web.** A Retention page (classes, holds, the queue with a typed confirmation, and the record) and a Retention dialog on each document.

## Not in this slice
- **What ADR 0015 asks for that is still to do:** crypto-shredding of the document's data keys (files are deleted, but keys are per stored file and shared under deduplication, so this needs the key design reworked), trigger dates from a metadata field or a workflow event, calendar rules in the tenant's time zone, approval through a workflow, an eligibility job that only marks records, privacy erasure requests, and recoverable user deletion.
- A second approver, or a review step between "eligible" and "destroyed".
- Retention policies per cabinet or document type, automatic assignment, and bulk assignment.
- Scheduled scans and notices before a document becomes eligible.
- Object-store locks, and proof of deletion from backups.
- Disposal certificates as downloadable documents, and rules for records that must be transferred rather than destroyed.

## Consequences
- Because everything is computed on demand, there is no stored state to fall out of date, at the cost of scanning classed documents when the queue is read. The queue looks at a bounded number.
- The disposal test creates data in every related table and checks it is all gone. A new table that points at a document must be added to the destroy order, and that test will fail until it is.
