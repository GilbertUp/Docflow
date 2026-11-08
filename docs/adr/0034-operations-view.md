# ADR 0034: An operator's view of usage, health and background jobs

**Status:** Accepted on 2026-11-08 (F19, first slice)

## Context
The job framework (ADR 0007) was built in M0 with a note that F19 would add a monitor. People who run the system need to see whether it is working, whether work is piling up, how it is being used and how much space it takes, and to restart work that has given up. They should not be able to learn what individual people are doing, or what is in documents or job payloads.

## Decision
- **One right.** `admin.operate` (migration 0020 adds it to existing installs) covers everything here. It is separate from `audit.read` and from the right to manage people, so an operator need not be able to read the audit trail or change who can do what.
- **Usage is aggregate.** Counts for the whole organization: documents, versions, people, stored bytes, bytes of smaller copies, documents without a type, text-reading states, open and overdue tasks, workflow runs by outcome, documents on hold, disposals in the period, and documents filed, searches made and questions asked per day for 1 to 365 days. Quiet days are filled with zeros so a chart cannot hide a gap. Cabinets are listed with their size. There is no ranking of people and no list of searches, and titles, field values and payloads never appear. A test checks that a document's name and text are absent from the response.
- **Stored bytes are counted once.** A file shared by several documents (ADR 0006) counts once in `stored_bytes`, and smaller copies (ADR 0032) are counted apart, so neither figure is inflated or hidden.
- **Health.** The database, queue, file storage and a worker heartbeat are each checked, and a failing part is reported as failing rather than failing the page. It also reports work piling up (jobs waiting more than five minutes), jobs that have given up, and whether the audit trail has been anchored outside the database in the last 48 hours. A missing anchor is reported because it is the thing that makes a rewritten trail detectable (ADR 0012).
- **Job monitor.** A summary by status with the longest wait and the types that are waiting or dead, and a list that can be filtered by status and type. Errors are cut to 300 characters, because an error can quote text from a file, and payloads are never shown.
- **Retry is the only action.** A job that failed or gave up can be retried, which gives it a fresh round of attempts and sends it to a worker again. Any other job is refused. It is audited as `job.retried` with the type, who did it, and what had gone wrong. Jobs belong to one organization, so another organization's jobs are not visible or retryable.
- **Web.** An Operations page: health, usage with simple bar charts and a period choice, and the job list with a try-again button. It refreshes health and jobs on its own.

## Not in this slice
- **Alerts.** Nothing tells anyone when a check fails. The page must be opened.
- **Platform-wide operations.** This is one organization's view. Seeing all organizations, queue depths in the broker, and worker counts needs a separate, more privileged role and is left to the operator tools.
- Trends beyond the period chosen, exports of analytics, and saved reports.
- Cancelling or editing jobs, and pausing queues.
- Settings pages for the organization (name, defaults, branding), user provisioning from a directory, and licence and quota management.
- Prometheus metrics for the new counts. The existing `/metrics` endpoint is unchanged.

## Consequences
- Because analytics read the whole organization with counts and groupings, they are heavier than ordinary pages. They are bounded by the period and by a limit on grouped lists, and are only read by operators.
- The retry changes state on the word of a person looking at a summary. Retrying a job whose cause has not been fixed just makes it give up again, which the page shows.
