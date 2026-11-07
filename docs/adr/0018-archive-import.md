# ADR 0018: ZIP import as a background job that acts as the submitter

**Status:** Accepted on 2026-11-07 (second slice of F03)

## Context
People arrive with a folder of files, not one file. Importing them one by one through the browser is slow and loses the folder structure. A ZIP archive is the common way to carry a folder. It is also the classic way to attack a document store: paths that escape the target, archive bombs, links, and thousands of entries.

## Decision
- **Submit, then work in the background.** `POST /cabinets|folders/{id}/imports?filename=` takes the archive as the body, checks that it is a readable ZIP within the limits, stages it encrypted, and queues a job on the `ingest` queue (ADR 0007), all in one transaction. It answers 202 with an import id. `GET /imports/{id}` reports status, counts and per-file results.
- **Acts as the submitter, at filing time.** The worker files each document as the person who started the import, with that person's rights when each file is filed. A right taken away before the worker reaches the file makes that file fail, and an import started by someone who has since been disabled does not run. The import is visible only to that person, not even to an administrator, because its file list shows paths from their archive.
- **The normal pipeline for every file.** Each file goes through the same upload path as a browser upload: type from the bytes, size limit, malware scan, deduplication, audit (ADR 0017). The archive itself is not scanned as one blob, since each entry is scanned as it is filed.
- **Folders from directories.** Directories in the archive become folders, made one level at a time and reused when they already exist. Folder creation follows the usual rules for names, depth and permissions.
- **The archive is untrusted.** Refused up front: more than `DOCUFLOW_IMPORT_MAX_ENTRIES` entries (1000), more than `DOCUFLOW_MAX_IMPORT_BYTES` (512 MiB), and anything that is not a valid ZIP. Refused per file, with the reason recorded: paths that are absolute, contain `..`, a drive letter or control characters, links, password-protected entries, repeated paths (compared without regard to case), and entries compressed more than 200 times. Operating-system litter (`__MACOSX`, `.DS_Store`, `Thumbs.db`, `._` files) is skipped silently. Data is also limited while it is being read, not only by what the archive declares: no single file may pass the upload limit, and filing stops once the total read passes `DOCUFLOW_IMPORT_MAX_EXPANDED_MB` (1024).
- **One bad file never stops the rest.** Each file ends as `filed` or `failed` with a message in `ingestion_item`. The job ends `completed` even when some files failed, and `failed` only when the whole archive could not be used.
- **Safe to retry.** Delivery is at least once. A rerun skips every path already recorded, so a crash halfway never files a document twice.
- **Staging is temporary.** The staged archive is encrypted like any stored object, never served, and deleted when the import ends, whether it completed or failed.

## Consequences
- The web client polls the import until it ends and then lists what was not filed.
- The demo server has no worker, so it runs the real outbox, dispatcher and job runner in a background thread, one job at a time.
- Large imports need the worker running. If it is down, imports wait in the queue and the readiness check reports the worker as missing.

## Not in this slice
- Importing a folder from disk or a watched folder, email ingestion and scanner hardware (the rest of F03 and F20).
- Importing files that need more rights than `create`, such as setting types or fields (F06).
- Cancelling an import, a list of past imports, and cleaning up empty folders left behind by files that failed.
- A global cap on concurrent imports per organization.
