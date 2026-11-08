# ADR 0032: Compressing scanned documents, judged page by page

**Status:** Accepted on 2026-11-08 (F17, first slice)

## Context
[ADR 0014](0014-compression-engine.md) sets the shape of the engine: analyse each page, choose a treatment, produce a copy, measure it, and let a quality gate decide, with the worst page deciding. This records what the first slice builds, what it does not, and one finding that changed the design.

## Decision
- **Pure engine, separate from storage.** `compression/engine.py` takes a file and a profile and returns a result. It knows nothing about the database, so every choice in it is tested on synthetic scans.
- **Scope.** Scanned PDFs, and single-frame PNG, JPEG, WebP and GIF images. A PDF with real text on any page is left alone and recorded as "no benefit", because turning it into pictures would lose the text, and no PDF engine is installed that could shrink it safely (ADR 0014 names pikepdf, which is not added yet). Multi-frame images, damaged files and files over the limits are also recorded as no benefit, never as an error.
- **Treatment per page.** A page is blank, line art (text and drawings) or photographic, from its tone histogram. Blank pages and line art become two-colour pictures with lossless coding, if that still passes the check. Otherwise, and for photographs, JPEG is tried from a quality ladder (80, 65, 50, 40) and the first level that passes is used. Lossy two-colour coding that swaps similar shapes (JBIG2 symbol matching) is never used, because it can change characters.
- **The gate.** Two checks per page, and the worst page decides. First, structural similarity (SSIM) between the page as it was at the working resolution and the page as a reader decodes the copy: 0.97 for text and line art, 0.92 for photographs, from the plan. Second, a display check: the finished PDF is drawn again by pdfium and compared at a quarter size against the original (0.85), so a copy that decodes well but does not display like the original is still refused. PSNR is recorded beside SSIM.
- **Outcomes.** `accepted` (passed, and at least 5% smaller), `rejected` (failed the check, kept with its numbers, never recommended or downloadable), or `no_benefit` (not clearly smaller, or not something this engine can shrink, nothing stored). The newest accepted copy of a version is the recommended one.
- **Reproducible.** Every run stores the profile name and version, the working DPI, thresholds and ladder, the Python, Pillow, pypdfium2 and NumPy versions, the SHA-256 of the file it started from, and per-page treatment and measurements. The same input and profile gives the same copy, and a test checks that.
- **Never touches the original.** A copy is a separate encrypted stored object tied to the version it came from, deduplicated like any file. It is made by a background job on the low-priority `compress` queue, asked for by someone who may edit the document, never started by itself. Downloading an accepted copy needs `download` and is audited. Disposal (ADR 0030) now removes copies and releases their stored files too.
- **Honest reporting.** `GET /compression/report` (needs `audit.read`, JSON or CSV) counts runs by outcome and gives the size saved by accepted copies. It reports `storage_reclaimed_bytes` separately and as zero, because originals are kept. The two numbers are never added.
- **Finding that changed the design.** The first version judged the finished copy only by drawing it again and comparing pixels. pdfium rounds a bitmap up and smooths images as it draws them, so a correct copy of a perfectly clean page scored 0.88 depending on the page's size in points. Measuring the decoded pixels directly is exact. The redraw is kept, at reduced size, as a check that the file displays, not as the measure of quality.
- **Web.** A Compress dialog on each document: make a copy, see each run with its size change, similarity and reason, per-page treatments, and download the recommended copy.

## Not in this slice
- OCR accuracy before and after (the plan's second text-page criterion), and ground-truth comparison on a research dataset.
- Native-text and vector PDFs, which need a PDF engine (pikepdf or similar) for lossless stream recompression and object cleanup.
- Region-based treatment of mixed pages. A mixed page gets the safer single treatment.
- Optional codecs (WebP, JPEG 2000), JBIG2 lossless, and de-noising and deskew.
- Batch and scheduled optimisation, and sampling for very large files.
- A policy that moves originals to a cheaper tier, which is the only way storage is actually reclaimed.
- Compressing every page of a multi-frame TIFF.

## Consequences
- The gate measures the loss from re-encoding at the working resolution (150 dpi by default). It does not measure loss from a drop in resolution below a scan's own, so a 600 dpi original compared at 150 dpi hides that reduction. The working DPI is recorded with every run so the number is not misread.
- NumPy is now a dependency, for the metrics.
- A copy of an already efficient file is usually "no benefit", which is a correct result and is counted as a run.
