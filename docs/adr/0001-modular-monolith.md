# ADR 0001: Modular monolith with enforced boundaries

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
The guide asks for modular, domain-oriented architecture and replaceable adapters. Microservices would add network failure modes, distributed transactions and memory cost on a 7.8 GB development machine.

## Decision
- One Python codebase, deployed as API, worker, dispatcher and scheduler processes from one image.
- Modules live under `docuflow.modules.*` and call each other only through a public `service` interface.
- `import-linter` enforces these contracts in CI, so boundaries cannot erode unnoticed.
- Cross-module reactions use domain events written to the outbox in the same transaction.

## Consequences
- Atomic transactions across modules remain simple, which matters for permissions and audit.
- OCR and compression scale separately as dedicated worker queues.
- A module can be extracted later because its interface is already explicit.
- A heavy dependency in one module enters the shared image. Conversion tools therefore run in a separate sandbox container.
