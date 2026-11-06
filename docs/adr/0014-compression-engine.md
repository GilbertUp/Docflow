# ADR 0014: Page-level compression with evaluated derivatives

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
Compression is a core research capability. It must preserve originals, adapt to content, and prove quality with repeatable measurements.

## Decision

### Pipeline
1. **Analyse** each page: text or scan, colour or grayscale, photographic or line art, effective resolution, noise, blank pages.
2. **Choose** a strategy from a versioned compression profile.
3. **Produce** a derivative, using pikepdf or the selected `PdfEngine`, plus Pillow and optional codecs detected at runtime.
4. **Measure** size, ratio, reduction, time, SSIM, PSNR and OCR accuracy before and after.
5. **Decide** through the quality gate.

| Content | Default strategy |
|---|---|
| Native PDF text and vectors | Lossless stream recompression and safe object cleanup |
| Text-heavy scan | Bitonal or lossless where readable; conservative settings |
| Line art | Lossless (PNG or bitonal) |
| Photographic | Controlled lossy (JPEG, optional WebP or JP2) |
| Mixed page | Region-based treatment, subject to the gate; otherwise the safer strategy |

JBIG2 lossy mode is excluded because symbol substitution can silently change characters. This matters for invoices and contracts.

### Quality gate
Metrics are computed on rendered pages at a fixed DPI, per page. The **worst page decides**, so a good average cannot hide one unreadable page. OCR is compared against the OCR of the original as baseline, and against ground truth in the research dataset.

A failed derivative is retained with its metrics, but can never become the recommended copy.

### Reproducibility
Each run stores profile version, parameters, algorithm and library versions, source and derivative hashes, and environment. Evaluation datasets and experiments are exportable as CSV and JSON.

### Scheduling
Batch and scheduled optimisation run on a dedicated low-priority queue, with concurrency and page limits.

## Consequences
- A derivative may be pointless after accounting for metrics and overhead. In that case, it is recorded as "no benefit" and not recommended.
- Storage is only reduced if policy later archives or removes other copies. The source remains, so **real savings initially come from serving and archive tiers, not removal of originals.** Reports must separate derivative size reduction from actual storage reclaimed, or the numbers will mislead.
- Metric calculation is CPU-heavy on large documents. Limits and sampling options apply, and sampling is recorded with the results.
