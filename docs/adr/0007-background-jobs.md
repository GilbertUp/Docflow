# ADR 0007: PostgreSQL job state, outbox, Celery execution

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
Jobs must be durable, retryable, idempotent and visible. Redis alone may lose queued work on failure, and enqueueing after a commit can drop a job if the process dies between the two steps.

## Decision
- `processing_job` in PostgreSQL is the **source of truth** for status, attempts, progress, errors and idempotency key.
- Enqueueing writes to an `outbox` table in the business transaction. A dispatcher publishes to Redis, so a job exists if and only if its transaction committed.
- **Celery** executes work with `acks_late`, on separate queues: `ingest`, `ocr`, `index`, `embed`, `compress`, `notify`, `integrations`.
- Each job has a deterministic idempotency key, for example `ocr:{version_id}:{engine}:{language}`. Derived rows use unique constraints, so a retry cannot duplicate them.
- A scheduler tick drives durable timers from the database using `FOR UPDATE SKIP LOCKED`. Celery beat holds no business state.
- A sweeper re-queues jobs whose heartbeat expired. After the retry limit, jobs go to `dead` and appear in the job monitor.

## Consequences
- Losing Redis loses no work; the dispatcher and sweeper rebuild the queue.
- Slightly more database load than a pure broker design, which is acceptable at this scale.
- Celery's weight is accepted for maturity and routing. Only the job module touches Celery, so it can be replaced.
