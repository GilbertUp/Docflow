# ADR 0020: Document types with versioned fields, and typed values

**Status:** Accepted on 2026-11-08 (F06)

## Context
Finding and processing documents depends on what is known about them: an invoice has a number, a supplier, a total and a due date. Those details differ by kind of document, change over time, and must stay trustworthy after the definition changes.

## Decision
- **A type owns versioned field sets.** `document_type` points at its current `index_schema_version`. Changing a type's fields publishes a new immutable version with its own `index_field` and `field_choice` rows. Nothing recorded is rewritten. A new version identical to the current one is refused.
- **Documents keep their version.** A document records the type and the schema version its values were written against. Reading it lays its values out on the type's current fields by key, and says it is outdated. Saving it moves it to the current version, and values for fields that no longer exist are dropped.
- **Five kinds of field.** Text, number, date, yes or no, and one of a list. Each has a label, a key (lowercase letters, digits and underscores), required and read-only flags, an optional default, and rules that fit its kind: pattern and length for text, range for numbers and dates. At most 40 fields per type and 100 choices per field.
- **Typed storage, exact numbers.** Values go in the column that fits their kind (`value_text`, `value_number` as `numeric(20,6)`, `value_date`, `value_bool`). Numbers are parsed as decimals, never floats, and returned as exact text. Dates are ISO.
- **Check everything, report everything.** A save validates all values and returns one message per field, with status 422 and the field names the form uses. A missing required value does not block a save, because filing happens in steps. The document reports what is still missing and whether it is complete.
- **Patterns are bounded.** Python's `re` has no timeout, so a pattern is limited to 200 characters, rejected if it repeats a repeat (such as `(a+)+`), must match the whole value, and is only run on values up to 500 characters.
- **Read-only fields belong to the system.** They are for values filled by extraction (F07) or calculation. A person cannot change them, and saving keeps them.
- **Defaults apply once.** A default fills a field only when a document is first given the type, never on later saves.
- **Rights.** Making or changing types needs the new system right `document_types.manage` (migration 0008 adds it to existing installs). Anyone signed in can list types. Reading a document's values needs `read`, and setting them needs `edit_metadata` and a document that is not archived or inside an archived folder. A new type cannot be given to documents once archived, but documents that already have it keep it.
- **Audit.** `document_type.created`, `.version_published` (with fields added and removed) and `.updated`, and `document.indexed` with the changed fields (values cut at 200 characters).

## Not in this slice
- **Allowed types per cabinet**, **visibility rules** and **calculated fields** from the domain model.
- **Reference fields and master data** (suppliers, employees) and **table fields** for line items.
- **Searching by field value.** The index column on text values is there for F08.
- **Moving an old document to a new version in bulk.** Documents move one at a time, when saved.

## Consequences
- Extraction (F07) can target fields by key and write `read_only` or suggested values without a schema change.
- The web client renders any type with one form, so new kinds of document need no new screens.
