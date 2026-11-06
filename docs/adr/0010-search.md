# ADR 0010: Hybrid PostgreSQL search with permissions in the query

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
Search must support exact, fuzzy, Boolean, phrase, range and semantic queries, always filtering unauthorised documents before results are ranked.

## Decision
- **Unit of indexing:** a page-level `search_chunk` with text, `tsvector`, embedding and the denormalised permission fields from [ADR 0004](0004-authorization-model.md).
- **Keyword:** PostgreSQL FTS with GIN indexes; a query parser for Boolean, phrase and field syntax.
- **Fuzzy:** `pg_trgm`.
- **Semantic:** pgvector HNSW with iterative scans enabled (available since 0.8.0), so permission filters do not leave too few results.
- **Fusion:** reciprocal rank fusion with configurable weights per tenant.
- **Multilingual text:** an `english`, `french` or `simple` configuration per chunk, based on detected language. Kinyarwanda uses `simple` initially.
- **Embeddings:** a local multilingual model through ONNX, with no external calls. Vectors record their model, and changing model runs a reindex job.
- **Adapter:** `SearchBackend` interface, leaving room for an OpenSearch implementation that must apply the same permission predicate.
- **Consistency:** indexing is asynchronous, but permission changes update chunks in the same transaction as the ACL change.

## Consequences
- No extra search service in the core stack.
- A user with few readable documents among many may get slower ANN scans. The planner can switch to exact scan on the filtered set, and this is covered in load tests.
- Result counts are computed after permission filtering, so totals cannot reveal hidden documents.
