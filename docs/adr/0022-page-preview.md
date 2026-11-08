# ADR 0022: Showing documents as server-rendered page images

**Status:** Accepted on 2026-11-08 (F05)

## Context
People need to look at a document in the browser. Sending the file would turn every viewer into a downloader and break the rule that download is a separate right from viewing.

## Decision
- **Images, never the file.** `GET /documents/{id}/preview` says how to show the current version (`pages`, `text` or `none`) and lists its pages. `GET /documents/{id}/preview/pages/{n}?width=` returns one page as a PNG, rendered on the server from the decrypted object. The original bytes never reach the browser.
- **Right needed:** `preview`, not `download`. A user who cannot see the document gets 404, and one who can see but not preview gets 403.
- **Only released files.** A file still in quarantine, or rejected by the scan, is not shown.
- **Supported kinds:** PDF (pdfium) and images, including multi-page TIFF. Plain text is shown through the text view. Word, Excel and PowerPoint report `none` until a converter is added.
- **Bounded rendering.** Width is limited to 64 to 2000 pixels, the longest side to 6000, and pages to 500. Responses are `private, no-store` so a decrypted copy is not kept in a shared cache.
- **Nothing stored.** Pages are rendered on request, so no unprotected copy exists on disk or in the object store.
- **Audit.** `document.previewed` with version and page, for reading-size images. Thumbnails (240 pixels or less) are not logged, to keep the log useful.
- **Web.** A View dialog with previous and next, thumbnails, and three sizes.

## Not in this slice
- Office formats and email, which need a converter (and a sandbox for it).
- Highlighting suggested values and found words on the page, using the boxes F07 stores.
- Rotating, annotating and watermarking.
- Rendering cache and a render queue for very large files.

## Consequences
- Large files are decoded on each request. A cache keyed by version and width can be added without changing the API.
