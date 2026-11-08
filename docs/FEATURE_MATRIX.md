# Feature completion matrix

**Last updated:** 2026-11-07

A feature is **Done** only when all ten criteria from the guide pass: model, migration, service, API, UI, authorization, audit, tests, documentation, and error handling.

**Status values:** Not started | In progress | Done, link pending | Done | Blocked (external)

| ID | Feature | Milestone | Status | Open link to a later feature | Evidence |
|---|---|---|---|---|---|
| M0 | Foundation (not a spec feature) | M0 | Done | | [Gate report](reports/m0-gate.md), CI run #6 |
| F01 | Identity, organizations and permissions | M1 | In progress | OIDC single sign-on adapter | Service, API and web screens (sign-in, MFA, users, groups, roles, API keys, permissions, inspector); 94 identity tests and 33 web tests pass locally; CI run #13 (commit b50a697) green; 27 browser journeys pass locally in Edge (isolation, immediate permission change, onboarding, security, accessibility), CI run #17 (commit a51cb30) green |
| F02 | Cabinets and folders | M1 | In progress | Upload for the acceptance demo (F03); retention (F15); auto-routing (F07, F11); document copy (F03) | Service, API and web browser (tree, breadcrumbs, create, rename, move, copy, archive, delete, permissions); 52 repository tests pass locally; CI run #13 (commit b50a697) green, including the web container and proxy checks; Finance and HR isolation journeys pass locally in a real browser, CI run #17 (commit a51cb30) green |
| F03 | Document ingestion | M2 | In progress | Import from a disk folder or watched folder; malware rescans; email ingestion (F20); document copy | Upload and audited download over HTTP and in the web client ([ADR 0017](adr/0017-upload-pipeline.md)); malware scanning with ClamAV, fail closed; new versions with history; ZIP import as a background job ([ADR 0018](adr/0018-archive-import.md)); backend, unit and browser tests pass locally, CI pending |
| F04 | OCR and text extraction | M2 | In progress | Word, Excel and PowerPoint text; Kinyarwanda (no Tesseract pack found); handwriting, tables and layout; accuracy measured on real scans | Text from PDFs, images and text files; OCR through an engine interface with Tesseract, in many languages chosen per run ([ADR 0019](adr/0019-text-extraction-and-languages.md)); backend, unit and browser tests pass locally, Tesseract tests and the image check run in CI only |
| F05 | Automatic preview | M2 | In progress | Audit timeline (F16); Office formats; highlighting found values on the page; browser journeys | Page images rendered on the server for PDF and images, thumbnails, size choice, preview right, scan-released only, audited ([ADR 0022](adr/0022-page-preview.md)); backend (466) and web (60) tests pass locally, CI pending |
| F06 | Document types and index fields | M2 | In progress | Allowed types per cabinet; visibility rules and calculated fields; reference fields and master data; table fields; search by field value (F08) | Types with versioned fields, typed validated values, per-field errors, required tracking, audit, and a web editor and details form ([ADR 0020](adr/0020-document-types-and-index-fields.md)); backend, unit and browser tests pass locally, CI pending |
| F07 | Intelligent indexing | M2 | In progress | Browser journeys for the new screens; learning from corrections; duplicate detection | Keyword type proposal, label and pattern extraction with page and word boxes, confidence thresholds, corrections kept ([ADR 0021](adr/0021-intelligent-indexing.md)); web editor for keywords, labels and levels, and suggestions in the details dialog; backend (461) and web (58) unit tests pass locally, CI pending |
| F08 | Search and retrieval | M3 | In progress | Accent-insensitive and field-value search; saved searches; reindex after rename; hybrid search (F18); browser journeys | Full-text search over titles, details and text with phrases, alternatives and exclusion; permission checked on every result; text matches need preview; type and cabinet filters; Search page ([ADR 0023](adr/0023-search.md)); backend (471) and web (63) tests pass locally, CI pending |
| F09 | Versions and relationships | M3 | In progress | Compressed derivative (F17); version comparison; duplicate links; browser journeys | Restore an earlier version as a new one, typed two-way links between documents that respect read rights, audit on both ends, versions and Links dialogs ([ADR 0024](adr/0024-restore-and-relationships.md)); backend (479) and web (63) tests pass locally, CI pending |
| F10 | Tasks and collaboration | M4 | In progress | Group assignment and delegation; workflow tasks (F11); email (F13); annotations; browser journeys | Comments with mentions, tasks with assignee, priority and due time, in-app notifications, all limited to people who can read the document; Discuss dialog, My tasks and Notifications pages ([ADR 0025](adr/0025-comments-tasks-notifications.md)); backend (488) and web (65) tests pass locally, CI pending |
| F11 | Workflow automation | M4 | In progress | Branching, parallel and group steps; timers and escalation (F13); signatures (F14); forms (F12); visual designer; browser journeys | Ordered approval and task steps, immutable versions, tasks made per step, reject with reason, start by hand or when a document is typed, delegation, history and audit ([ADR 0026](adr/0026-workflows.md)); backend (499) and web (69) tests pass locally, CI pending |
| F12 | Forms and process data | M4 | In progress | Start a workflow from a submission; conditional and file fields; export; narrower audiences; browser journeys | Versioned forms using the document-type field checks, submissions validated as a whole and kept per version, own-or-manage visibility, attach to a document, Forms and Manage forms pages ([ADR 0027](adr/0027-forms.md)); backend (507) and web (72) tests pass locally, CI pending; one unrelated jobs test failed once under load and passed on rerun |
| F13 | Notifications and SLA | M4 | In progress | Business calendars; several policies; digests and templates; admin delivery view; clean-up of spent timers; browser journeys | Opt-in email per kind with tracked delivery and retries, one place that makes notifications, reminder, overdue and escalation timers for due tasks, SLA policy, preferences and policy screens ([ADR 0028](adr/0028-notifications-and-deadlines.md)); backend (515) and web (74) tests pass locally, CI pending |
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
