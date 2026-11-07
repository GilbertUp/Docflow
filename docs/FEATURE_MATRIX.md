# Feature completion matrix

**Last updated:** 2026-11-07

A feature is **Done** only when all ten criteria from the guide pass: model, migration, service, API, UI, authorization, audit, tests, documentation, and error handling.

**Status values:** Not started | In progress | Done, link pending | Done | Blocked (external)

| ID | Feature | Milestone | Status | Open link to a later feature | Evidence |
|---|---|---|---|---|---|
| M0 | Foundation (not a spec feature) | M0 | Done | | [Gate report](reports/m0-gate.md), CI run #6 |
| F01 | Identity, organizations and permissions | M1 | In progress | OIDC single sign-on adapter; end-to-end browser demo | Service, API and web screens (sign-in, MFA, users, groups, roles, API keys, permissions, inspector); 94 identity tests and 33 web tests pass locally; CI run #13 (commit b50a697) green |
| F02 | Cabinets and folders | M1 | In progress | Upload for the acceptance demo (F03); retention (F15); auto-routing (F07, F11); document copy (F03); end-to-end browser demo | Service, API and web browser (tree, breadcrumbs, create, rename, move, copy, archive, delete, permissions); 52 repository tests pass locally; CI run #13 (commit b50a697) green, including the web container and proxy checks |
| F03 | Document ingestion | M2 | Not started | Email ingestion (F20) | |
| F04 | OCR and text extraction | M2 | Not started | | |
| F05 | Automatic preview | M2 | Not started | Audit timeline (F16) | |
| F06 | Document types and index fields | M2 | Not started | | |
| F07 | Intelligent indexing | M2 | Not started | | |
| F08 | Search and retrieval | M3 | Not started | | |
| F09 | Versions and relationships | M3 | Not started | Compressed derivative (F17) | |
| F10 | Tasks and collaboration | M4 | Not started | | |
| F11 | Workflow automation | M4 | Not started | | |
| F12 | Forms and process data | M4 | Not started | | |
| F13 | Notifications and SLA | M4 | Not started | | |
| F14 | Approval, signature and stamp | M4 | Not started | Real provider needs owner account | |
| F15 | Retention and legal hold | M5 | Not started | | |
| F16 | Audit and compliance | M5 | Not started | | |
| F17 | Compression engine | M6 | Not started | | |
| F18 | AI assistant and RAG | M6 | Not started | Generated answers need a provider | |
| F19 | Administration and analytics | M7 | Not started | | |
| F20 | Integrations and API | M7 | Not started | Real connectors need owner accounts | |
| F21 | Backup, export and restore | M7 | Not started | | |
| F22 | Security hardening and production | M7 | Not started | | |

## End-to-end scenarios

| Scenario | Depends on | Status |
|---|---|---|
| A. Invoice ingestion to approval | F01 to F14 | Not started |
| B. HR employee file | F01 to F08 | Not started |
| C. Intelligent retrieval | F01 to F09 | Not started |
| D. Compression | F03, F04, F17 | Not started |
| E. Retention and legal hold | F11, F15, F16 | Not started |
| F. AI knowledge assistant | F01, F08, F18 | Not started |
