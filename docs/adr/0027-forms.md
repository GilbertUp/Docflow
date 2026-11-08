# ADR 0027: Forms with versioned fields and checked submissions

**Status:** Accepted on 2026-11-08 (F12, first slice)

## Context
Some process data does not start from a file: a leave request, an expense claim, a request for access. People fill in the same details each time, and the answers must be as trustworthy as the details recorded on a document.

## Decision
- **Same fields, same checks as document types.** A form's fields are text, number, date, yes or no, and one of a list, with the same rules (pattern, length, range, choices), the same key and label checks, and the same value parsing as F06. A value therefore means the same thing wherever it is entered. The code is shared, not copied.
- **Versions are immutable.** Changing fields publishes a new version, and a submission records the version it was made on. A version identical to the current one is refused. The form's `row_version` stops two managers overwriting each other.
- **Rights.** Making and changing forms needs the new system right `forms.manage` (migration 0014 adds it to existing installs). Anyone signed in can list active forms and fill one in. A person sees their own submissions, and someone with `forms.manage` sees all of them.
- **Checked as a whole.** A submission is validated against its version and every problem is returned at once, by field key, including unknown keys and missing required answers. Answers are stored as text in each field's own form (numbers exact, dates ISO, yes or no), or null when left empty.
- **Attach to a document.** A submission can name a document. That needs `annotate` on it, and anyone who can read the document can then see the submission there. A document the caller cannot read is "not found".
- **Audit.** `form.created`, `form.version_published` (fields added and removed), `form.updated`, `form.submitted`, and `document.form_submitted` on the document when attached.
- **Web.** A Forms page and a fill-in page for everyone, with the person's earlier submissions, and a Manage forms page that reuses the field editor of document types.

## Not in this slice
- Starting a workflow from a submission (F11 workflows begin from a document, so a form needs to make one first), and forms inside workflow steps.
- Public or anonymous forms, audiences narrower than "everyone signed in", and sections, conditional fields and layout.
- File upload fields, reference fields and tables of rows.
- Editing or withdrawing a submission, exporting submissions, and reports over them.

## Consequences
- Because forms reuse document-type validation, any new kind of field added to F06 is available to forms at once.
- Submissions are plain records. Turning them into documents or workflow runs is a separate step to design.
