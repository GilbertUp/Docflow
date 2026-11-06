# ADR 0003: Tenant isolation through PostgreSQL row-level security

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
The guide requires multi-tenancy at the database layer. Filters in application code are one forgotten `WHERE` away from a cross-tenant leak.

## Decision
- Every tenant-owned table has `tenant_id`, with `ENABLE` and `FORCE ROW LEVEL SECURITY`.
- Policies compare `tenant_id` with `current_setting('app.tenant_id')`. It is set with `set_config(..., true)`, which is transaction-local and safe with PgBouncer.
- Three database roles: `docuflow_migrator` owns the schema; `docuflow_app` is used at runtime and cannot bypass RLS; `docuflow_platform` handles the few cross-tenant operations such as tenant creation and is audited.
- Tenant comes from the authenticated principal, never from a client-supplied header.
- A CI test lists all tables and fails if any tenant-owned table lacks a policy.
- Workers receive `tenant_id` in the job and set it in the same way.

## Consequences
- A missed filter returns nothing rather than another tenant's data.
- Composite indexes must lead with `tenant_id`.
- Connection setup needs care: a missing setting must fail closed, which is tested.
