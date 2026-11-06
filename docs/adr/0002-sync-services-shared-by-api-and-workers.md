# ADR 0002: Synchronous SQLAlchemy, services shared by API and workers

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
Domain logic must run identically in API requests and background jobs. Async SQLAlchemy would force two code styles, because Celery workers and most document libraries are synchronous.

## Decision
- Python 3.12 in containers, FastAPI, SQLAlchemy 2.x with psycopg 3 in synchronous mode.
- FastAPI runs database routes in its thread pool. Upload and download streaming use async routes with no database session held open during transfer.
- Services accept a unit-of-work object and never import web or Celery types.
- PgBouncer in transaction mode is supported from the start; session-level state is avoided.

## Consequences
- One way of writing every service, and easier tests.
- Thread-pool size bounds concurrency, so slow work must go to jobs. This is already required.
- Python 3.12 is deliberate even though 3.14 is installed locally, because document and ML packages tend to support newer versions late.
