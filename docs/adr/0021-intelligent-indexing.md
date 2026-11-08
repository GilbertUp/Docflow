# ADR 0021: Proposing a type and field values from a document's text

**Status:** Accepted on 2026-11-08 (F07, backend slice)

## Context
Typing every document and filling every field by hand does not scale. The text of a document is already read (F04), and F06 gives each type fields with keys. We need proposals people can trust and check, without inventing values.

## Decision
- **Rules, not guesses.** A type has keywords. A field has extraction rules: labels (words that precede the value) and an optional pattern. No model is involved. Every suggestion comes from a rule applied to the document's own text.
- **Source is kept.** Each suggestion records the page, the character range and the word boxes it covers, the method (`label`, `pattern`, `label+pattern`) and a confidence, so a screen can highlight it.
- **Confidence decides.** Each type has an auto-accept and a review threshold (default 0.9 and 0.6). At or above the first, a valid value is filled into an empty field. Between them it waits for a person. Below the second it is shown as a weak guess. A type is assigned automatically only at the auto-accept bar, and only when it has a published version.
- **A person always wins.** Values already set are never overwritten. Saving a document settles each suggestion: matching accepts it, differing records a correction in `extraction_feedback` (kept for later learning), clearing it rejects it.
- **A job, after reading.** Finishing a version's text queues `indexing.extract_fields` on the `index` queue. It skips a version that a newer one replaced, and can be run again with `POST /documents/{id}/suggestions/run`. Reading it back is `GET /documents/{id}/suggestions`.
- **Safe patterns.** Extraction patterns pass the same checks as validation patterns (length, no repeated repeats).
- **Audit.** `document.fields_suggested` with the type, whether it was applied, the confidence, and the keys filled.

## Not in this slice
- Learning from feedback, machine-learned classification, duplicate detection.
- Web screens for keywords, rules and reviewing suggestions.
- Routing into folders by type (F02/F11).

## Consequences
- Extraction needs no schema change when types change, and search (F08) can rely on the filled values.
- Corrections are recorded, so a later learning step has data.
