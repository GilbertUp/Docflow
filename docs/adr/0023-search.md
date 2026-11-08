# ADR 0023: Keyword search that respects permissions

**Status:** Accepted on 2026-11-08 (F08, keyword slice)

## Context
People need to find documents by what they are called, what was recorded about them, and what they say. Results must never reveal documents, or words inside documents, that the person may not see.

## Decision
- **PostgreSQL full-text search.** One `search_entry` row per document holds the details (title, type, field values) and the page text apart, each with a generated `tsvector` and a GIN index. No separate search server is needed at this stage.
- **Index is a copy, rebuilt by a job.** `search.index_document` runs on the `index` queue when a version is filed (so the title is findable at once), when its text has been read, and when details are saved. Archived or deleted documents lose their row.
- **Permissions on every candidate.** The query returns up to 500 best matches, and each is checked with the same `read` decision used everywhere else. Totals count only what the caller may read.
- **Text needs `preview`.** A document that matches only through its text is shown only to someone with `preview` on it. Snippets are cut only from text that person may see, and details-only matches carry no snippet. This stops a reader without preview from probing a document's content by searching.
- **Plain-words query language.** `websearch_to_tsquery`: every word must be present, quotes make a phrase, `or` makes alternatives and `-` excludes. The `simple` configuration is used because content is English, French and Kinyarwanda, and no language's stemming fits all three.
- **Snippets use `[[match]]` marks,** not HTML, so the client builds the highlighting and nothing in the text can inject markup.
- **Audit.** `search.performed` records the query, the result count and the type filter.
- **Filters:** document type and cabinet. Paging by limit and offset.

## Not in this slice
- Accent-insensitive matching (needs the `unaccent` extension and an immutable wrapper), stemming and typo tolerance.
- Searching inside one field's value (`total > 1000`), date ranges, saved searches.
- Semantic and hybrid search (F18). Page-level results with a link to the page (F05 can show it).
- Re-indexing after a rename or move, which waits for the next change to the document. A bulk reindex command is also still to do.
- Results beyond the best 500 candidates.

## Consequences
- The 500-candidate limit bounds the cost of permission checks. A person who can read very little of a very large match set may see fewer results than exist. Moving permission checks into the query would remove this, at the cost of duplicating the policy in SQL.
