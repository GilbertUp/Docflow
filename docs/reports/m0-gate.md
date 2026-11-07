# M0 gate report

**Result:** Passed on 2026-11-07 | **CI run:** #6 | **Commit:** `bf50708`

Evidence comes from GitHub Actions on clean Ubuntu 24.04 runners. The stack was not run on the owner's laptop, because WSL cannot start there (see Open items).

| Gate item | Result |
|---|---|
| Stack starts from clean with Compose | Passed |
| Readiness: database, queue, storage | Passed |
| Backend tests on full PostgreSQL | Passed |
| Lint, types, module boundaries | Passed |
| Licence policy and vulnerability audit | Passed |
| Migration upgrade, downgrade, upgrade | Passed |
| Live job: scheduler, outbox, dispatcher, Redis, Celery, worker, storage | Passed |
| Storage suite on real SeaweedFS | 13 of 13 passed |
| S3 conformance: versioning, object lock, multipart | 3 of 3 passed |

## Measured memory

Taken at the end of the CI run, with almost no data loaded. OCR and larger documents will use more.

| Service | Used | Limit |
|---|---|---|
| SeaweedFS | 176 MiB | 768 MiB |
| Worker | 174 MiB | 768 MiB |
| API | 93 MiB | 384 MiB |
| Dispatcher | 89 MiB | 192 MiB |
| Scheduler | 82 MiB | 192 MiB |
| PostgreSQL | 37 MiB | 512 MiB |
| Redis | 4 MiB | 128 MiB |
| **Total** | **about 655 MiB** | |

## Problems the gate found

| Problem | Fix |
|---|---|
| Error responses lost correlation ID and security headers | Errors handled inside the request middleware |
| Worker crashed in a clean image: `packaging` undeclared by kombu | Declared the dependency; the image build now imports the transport |
| SeaweedFS could not serve a second bucket with `-volume.max=8` | Raised to 100 |
| A hung step could run for hours | Added job, step and per-test time limits |
| The repository was public for a short time | The owner made it private. No secrets were committed; the commit email was exposed during that time |

## Open items

- **Readiness ignores workers.** The API reported ready while the worker was down. To be fixed at the start of M1.
- **Local Docker is blocked.** WSL fails with `0x80070569`, most likely a Group Policy logon right. The owner's IT team needs to confirm this.
- **Embedded PostgreSQL lacks `ltree` and `pg_trgm`.** Tests that need them, starting with F02, run in CI only.
- **Object lock coverage is basic.** One test proves a locked version cannot be deleted. Retention-period expiry and governance bypass are tested with F15.
- **Backup of keys is not built yet.** It belongs to F21.
