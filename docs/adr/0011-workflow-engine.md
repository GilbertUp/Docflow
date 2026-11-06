# ADR 0011: Custom token-based workflow engine

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
The guide requires a genuine configurable engine with parallel branches, timers, escalation and crash recovery. Temporal or Camunda would add a large service and memory cost, and their designers do not fit the document-centred domain. A simple status field cannot express parallel branches.

## Decision
- Definitions are versioned JSON graphs of nodes and edges, validated on publish. Running instances stay pinned to their version.
- Runtime uses **tokens**. A parallel split creates tokens; a join waits under a rule (all, any, or N of M).
- Node types: start, task, approval, condition, parallel split, join, timer, service call, webhook, notification, sub-action on document, end.
- Every transition runs in one transaction: lock the instance, advance tokens, create tasks and timers, write `workflow_event`, enqueue side effects through the outbox.
- Timers live in PostgreSQL ([ADR 0007](0007-background-jobs.md)), so they survive restarts.
- **Conditions are structured JSON expressions** with a fixed operator set. There is no `eval` and no user code. This also lets the designer build them visually.
- **Routing** resolves to a user, group, role, document-field value, or manager. Delegation records both the delegate and the original assignee.
- **Side effects** are idempotent, with retries, and failures go to an exception state an administrator can resolve.
- Outbound calls apply SSRF protection: a host allowlist, no private ranges, and DNS pinning.
- The designer uses React Flow on the web client; the engine validates definitions independently.

## Consequences
- We own a workflow engine, so its test suite is a major deliverable: parallel joins, timeouts, escalation, retries, crash recovery.
- Eleven starter templates ship as seed data, using only the public definition format, which proves nothing is hard-coded.
- BPMN import is not included.
