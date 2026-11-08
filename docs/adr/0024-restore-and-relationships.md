# ADR 0024: Restoring versions and linking documents

**Status:** Accepted on 2026-11-08 (F09, first slice)

## Context
Versions already keep every file a document has had. People also need to go back to an earlier one, and to say how documents relate, for example an invoice that answers a purchase order.

## Decision
- **Restore adds, never rewrites.** `POST /documents/{id}/versions/{n}/restore` adds a new version that points at the same stored file (its reference count goes up), with the reason "Restored from version n". History stays complete and numbers only grow. It needs the `version` right, refuses a version that is already current or whose file equals the current one, and starts reading the text again. Audited as `document.version_restored`.
- **Typed links.** `document_relationship` stores a link once, in the direction it was made. Types are `related` (the same both ways), `supersedes`, `responds_to` and `attached_to`. Reading a document shows the links it makes and the links made to it, each worded for the side being read ("Responds to" and "Answered by").
- **Rights.** Making a link needs `edit_metadata` on the document it starts from and `read` on the other. A link is listed only if the caller may read the other end, and an unreadable target is "not found", so links cannot be used to probe for hidden documents. Removing needs `edit_metadata` on an end the caller can read.
- **Guards.** No self-links, no duplicates (a `related` link also counts in reverse), at most 200 links started per document, and links go away with their documents.
- **Audit.** `document.link_added` and `document.link_removed` are written on both documents.
- **Web.** Restore buttons in the versions dialog, and a Links dialog on each document that finds the other document with search.

## Not in this slice
- Comparing two versions side by side, and version labels or pinning.
- Duplicate detection links (`duplicate_link`) and attachments as files rather than documents.
- Following links in bulk, graph views, and rules that act on links (F11).
- The compressed derivative of a version (F17).

## Consequences
- Because restore reuses the stored file, it costs no storage and cannot differ from the original.
