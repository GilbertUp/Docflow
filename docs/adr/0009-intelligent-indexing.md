# ADR 0009: Layered extraction with mandatory evidence

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
The spec forbids fake extraction, requires a working system with no AI subscription, and requires confidence and source page for every value. It also calls for tests that prevent invented values.

## Decision

### Classification pipeline
Stages run in order, stopping when confident: tenant rules; nearest-neighbour on local embeddings of validated examples; optional LLM. The result records stage, model version and confidence. A user's type selection overrides it and re-runs extraction.

### Extraction layers
| Layer | Requires AI? | Method |
|---|---|---|
| 1. Rules | No | Regex and label-anchor rules configured per field |
| 2. Layout | No | Label-to-value geometry from OCR boxes; table detection |
| 3. Learned templates | No | Anchors learned from validated corrections |
| 4. LLM | Optional | Structured output validated against the field schema |

Layers 1 to 3 form the deterministic fallback.

### Evidence rule
**A value without evidence is rejected.** Every candidate must resolve to a span in the extracted text, with page and coordinates. LLM values not found in the source are discarded and counted. Calculated fields are labelled as calculated.

### Confidence and routing
Field confidence combines OCR confidence, method reliability and validation results. Thresholds are set per document type:
- **High, and validation passes:** auto-accepted.
- **Medium:** human review.
- **Low:** correction required.

A failed validation or master-data mismatch never auto-accepts, whatever the score.

### Learning without poisoning
Corrections are stored as `extraction_feedback` with user, version and before and after values. Learned templates are versioned, activate only after a configurable number of agreeing confirmations, and can be rolled back. One user's correction cannot silently change results for everyone.

### Normalisation
Dates, amounts and currencies are normalised using locale hints, and the raw text is retained. Ambiguous dates such as 03/04/2026 are sent to review rather than guessed.

## Consequences
- Useful without any AI provider, and each value is checkable.
- Rule-based quality on unfamiliar layouts starts modest and improves with corrections.
- Field values are stored in a typed value table, with one version per field definition, so documents stay interpretable after schema changes.
