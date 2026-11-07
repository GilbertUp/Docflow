# ADR 0019: Reading text from files, with OCR in many languages

**Status:** Accepted on 2026-11-07 (first slice of F04). Builds on [ADR 0008](0008-document-processing-and-viewer.md).

## Context
Search, indexing and field extraction all start from text. Some files already contain it, such as a PDF made by an accounting package. Others are pictures of text: a scan, a phone photo, a fax. The owner asked that recognition work in many languages, not only English and French.

## Decision
- **A job per version, started when it is filed.** Filing a version (upload, import or new version) queues `documents.extract_text` on the `ocr` queue in the same transaction, and the version's `text_state` moves `pending`, `running`, then `done` or `failed`. File types with nothing to read yet, such as Word and Excel, are marked `unsupported` and queue nothing. Reading never blocks filing.
- **Use the text that is there.** For a PDF, each page's own text is used when it looks like real text (enough characters, mostly readable). Only pages without it are rendered and recognised, so a mixed PDF is recognised on its scanned pages alone. Plain text and CSV are their own text. Images, including every frame of a multi-page TIFF, are recognised. Blank pages are noted and skipped.
- **One engine interface, Tesseract by default.** `OcrEngine` returns text, word boxes and confidence. `TesseractEngine` runs the `tesseract` program with no shell, one page image at a time, with a timeout, reading its TSV output. A crash or hostile image can only take down that one run. A different engine can be added behind the same interface.
- **Many languages.** Tesseract reads more than a hundred languages, each from an installable pack. The set a server can read is a property of its image: `OCR_LANGUAGES` at build time chooses the packs, and the default list covers 43 widely used languages, including Arabic, Chinese (simplified and traditional), Hindi, Swahili, Amharic, Russian, Turkish and Vietnamese. `GET /ocr/languages` reports what is installed.
- **Languages are chosen per run.** With none chosen, `DOCUFLOW_OCR_DEFAULT_LANGUAGES` applies (English and French). A person with `edit_metadata` can read the current version again in up to `DOCUFLOW_OCR_MAX_LANGUAGES_PER_RUN` languages (5) at once. The limit exists because every extra language makes recognition slower and a little less accurate. The code lists are validated, so nothing reaches the command line unchecked.
- **Results are kept, newest wins.** Each page has a row per engine and language set (`ocr_result`), with text, word boxes in page pixels, mean confidence, engine version and time taken. Reading again adds a row, and the newest row is the current text. Text taken from the file itself is stored with engine `native` and confidence 1.
- **Text is content.** `GET /documents/{id}/text` needs the `preview` right, answers 404 for a document the caller cannot read, and is audited as `document.text_viewed`. The worker records `document.text_extracted` or `document.text_failed` with page counts, languages and the reason.
- **Failure is visible and bounded.** A damaged or password-protected PDF, an unreadable image, a missing language pack, or an engine that is not installed ends the version as `failed` with a short message that the page shows. Page count, image size and per-page time are limited. The job does not retry, because reading again is a deliberate action.

## What this does not do
- **Kinyarwanda.** No Tesseract language pack for Kinyarwanda was found. A file in Kinyarwanda read with a related language will be poor. The plan already says to measure this on real samples and train a model if needed. The engine interface lets a different engine take over a language.
- **Handwriting, tables and layout.** Output is text and word boxes. Table structure, reading order across columns and handwriting need other models and come later.
- **Word, Excel and PowerPoint text.** Their native text needs separate readers and a licence check. Until then they show as `unsupported`.
- **Orientation and script detection.** Pages are assumed upright and are read in the languages chosen. Automatic orientation and script detection are not built.
- **Accuracy figures.** The tests prove that the pipeline works and, where Tesseract is installed, that it reads clean English, French, German and Spanish test pages. They do not measure accuracy on real scans. That needs sample documents (plan decision 9).

## Consequences
- The backend image grows by the language packs. The default list adds to its size (not yet measured), and `OCR_LANGUAGES` shrinks it. Debian's packs hold Tesseract's "fast" models, which trade some accuracy for speed. Better "best" models can be mounted if accuracy matters more.
- The demo server and developer laptops usually have no Tesseract. Files with text still work there, and scans fail with a message saying so.
- CI installs Tesseract with English, French, German and Spanish for the engine tests, and the compose job runs `docuflow ocr-check` inside the real worker image to prove its engine and packs work.
