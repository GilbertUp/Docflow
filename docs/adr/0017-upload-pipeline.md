# ADR 0017: Raw-body upload, type from bytes, deduplication that does not leak

**Status:** Accepted on 2026-11-07 (first slice of F03)

## Context
F03 turns the filing structure into a place that holds files. Uploads are the largest and most hostile input the API accepts, and ADR 0006 already fixes how bytes are stored: hashed, encrypted, immutable, deduplicated per organization.

## Decision
- **The upload is the request body.** `POST /cabinets/{id}/documents?filename=` and `POST /folders/{id}/documents?filename=` take the file as `application/octet-stream`. This avoids a multipart parser and its extra dependency, and lets the browser stream a `File` without reading it into memory. The server spools the body to a private temporary file, so a slow client never holds a database transaction.
- **Permission first.** The caller's `create` right on the destination is checked before the body is read, and again inside the filing transaction.
- **Type from the bytes.** PDF, PNG, JPEG, GIF, TIFF and WebP are recognised by signature. Word, Excel and PowerPoint files share the ZIP signature, so the extension separates them. Text and CSV need a UTF-8 sample with no NUL bytes. Anything else gets 415 and nothing is stored. The filename is metadata and never reaches a storage key.
- **Size limit.** `DOCUFLOW_MAX_UPLOAD_BYTES`, 100 MiB by default, checked against the declared length and again while receiving.
- **Store outside the transaction.** Bytes are stored first, then one short transaction creates the stored-object row, the document, version 1 and the audit event. If that fails, the stored object is deleted. A race between two identical uploads falls back to sharing the first one's object.
- **Deduplication.** A duplicate upload creates a new document that shares the stored object, with a reference count. The response says `duplicate: true`, but names the earlier document only if the caller may read it, so the flag cannot be used to probe other departments' files.
- **Download.** `GET /documents/{id}/download` needs `download`, answers 404 for a document the caller cannot read, is audited, and streams the decrypted original as an attachment.

## Malware scanning
- **Scan before storing.** The scanner sees the plaintext, so it runs before encryption, on the spooled file. An infected file is never stored: the upload answers 422 `malware_detected`, nothing is filed, and an audit event `document.upload_blocked` records the file name, content hash, signature and scanner.
- **Fail closed.** If the scanner cannot answer (unreachable, timeout, or an error reply such as clamd's stream limit), the upload answers 503 `scanner_unavailable`. A file is never released unscanned because the scanner was down.
- **ClamAV by the clamd protocol.** `ClamdScanner` speaks INSTREAM over TCP and needs no client library. clamd's `StreamMaxLength` must be at least the upload limit; the compose service sets it to 100M.
- **Production must scan.** `DOCUFLOW_SCANNER` defaults to `none` for development and tests, and settings refuse `production` with `none`. Each version records `scan_engine`, so files that were never scanned stay identifiable. Versions filed before this change show `none`.
- **No quarantine queue.** Because infected files are refused rather than held, the `quarantined` state and a review queue are unused. They can return if the owner wants infected files kept for analysis, which would need a separate, locked-down store.
- **Rescans.** Signatures change, so already stored files may later match. A periodic rescan job belongs with the job framework and is not built.

## Not in this slice
- **Zip-bomb and archive limits** for Office files, and sandboxed conversion (ADR 0008), arrive with F04 and F05.
- **New versions, batch and folder import, email ingestion, document copy and the duplicate-link table.** `ingestion_job` is also unused until imports exist.

## Consequences
- No new runtime dependency.
- Scanning adds the time of one pass over the file to every upload.
- Deduplicated objects are deleted only when no version refers to them. Deleting documents is not built yet, so nothing decrements the count.
