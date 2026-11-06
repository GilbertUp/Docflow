# ADR 0008: PDF libraries, sandboxed conversion, image-based viewer

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
PyMuPDF is licensed under AGPL 3.0 or a paid Artifex commercial licence. A networked product that links it may need to offer its source or buy a licence. Untrusted files are also a major attack surface, and a PDF sent to the browser can be saved, defeating a download restriction.

## Decision

### PDF library
| If DocuFlow is... | Use |
|---|---|
| Closed-source or commercial (default) | **pypdfium2** (Apache-2.0 or BSD-3-Clause) for rendering and text, **pikepdf** for PDF rewriting |
| Open source under a compatible licence, or a commercial licence is purchased | PyMuPDF |

A `PdfEngine` interface isolates this choice. The pikepdf licence and all others are confirmed by the M0 licence scan.

### Format pipeline
- Content is sniffed from bytes; the extension is advisory only.
- Native text comes from pypdfium2, `python-docx`, `openpyxl`, `python-pptx` and the standard `email` library.
- OCR runs per page when native text is missing or unreliable. A mixed PDF gets OCR only on scanned pages.
- `.msg` support depends on a library licence check. Some candidates are GPL.
- Archives have limits on depth, entry count and expansion ratio.

### OCR
An `OcrEngine` interface with Tesseract as the default and PaddleOCR as an adapter. Handwriting uses the same interface through an optional adapter that needs larger models. Results store page, bounding boxes, confidence, engine and version.

### Sandbox
LibreOffice runs in a separate container with no network, a read-only root, memory and time limits, and a non-root user. Every job gets a new temporary profile.

### Viewer
- The browser receives server-rendered page images and a JSON text layer with coordinates.
- Those images are served through authorised, short-lived endpoints requiring `preview`.
- Highlights, annotations and OCR regions are drawn as overlays.
- Original bytes are available only from the audited download endpoint requiring `download`.

## Consequences
- The download restriction is real. Note that no viewer can stop screenshots; a watermark option reduces that risk.
- Page images add storage and pre-render work. Large documents render on demand.
- Native PDF text selection is replaced by the overlay.
