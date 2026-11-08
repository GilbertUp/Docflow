# ADR 0029: Approvals, internal signatures and stamps tied to a version

**Status:** Accepted on 2026-11-08 (F14, internal slice)

## Context
A decision about a document is only worth something if it is clear which document it was about. Documents get new versions, so an approval of version 1 must never read as approval of version 2. People also need to sign, and to mark documents as approved or paid, without anyone mistaking those for legally qualified signatures or for edits to the file.

## Decision
- **Everything is bound to bytes.** An approval, a signature and a stamp each record the version they were made on. Approvals and signatures also record the SHA-256 of that file. Reading them says whether that version is still the document's current one (`current`). A new version never inherits any of them. Signatures also report `intact`, which is whether the stored file still has the hash that was signed.
- **Workflow approvals are recorded automatically.** Deciding an approval step in a workflow (F11) writes an `approval` with the decider, the decision, the reason and the version hash, in the same transaction.
- **Internal signatures are attestations, and say so.** A signed-in person who may see the document's content (`preview`) signs the current version by giving their password again. The record has kind `internal_attestation`, a statement, and the file hash. It is never presented as an external e-signature. A person signs a version once at a time.
- **No password guessing through signing.** A wrong password counts toward the same lockout as signing in (five failures, fifteen minutes). The failed attempt is saved before the refusal is returned, so it still counts, and is audited as `signature.refused`. Only a person's own session can sign, not an API key.
- **Signatures are never edited or deleted.** The signer can revoke with a reason, and the revocation (time and reason) stays beside the signature. Anyone who can read the document can see its signatures.
- **Stamps are marks, not edits.** A stamp template (name, text up to 30 characters, one of four colours) is managed with `workflows.manage`. Applying one to a page of the current version needs `annotate`. The text and colour are copied when applied, so archiving or changing a template never changes a stamped page. Stamps are drawn onto the page images the viewer shows (ADR 0022), and the stored file is never changed. The person who applied a stamp, or anyone with `edit_metadata`, can remove it. A page holds at most 20.
- **Audit.** `signature.created`, `.refused`, `.revoked`, `stamp.created`, `.updated`, `document.stamped` and `document.stamp_removed`. Approvals appear in the existing `workflow.step_decided` event as well.
- **Web.** A Sign dialog (approvals, signatures, sign and revoke), stamp placement in the viewer by clicking the page, and stamp templates on the Workflows page.

## Not in this slice
- **External e-signatures.** The `external_esignature` kind, provider fields and the callback table are in the model, but no provider adapter is built. Proving one end to end needs the owner's provider account, so it stays listed as an external dependency.
- Burning stamps into a downloadable copy, and signing a stamped copy (needs a derivative, F17).
- Signature policies such as "two signatures needed", ordering, and signing on behalf of someone.
- Visual signature fields and positions, and long-term validation of signatures.

## Consequences
- Because stamps are applied at view time, they cost nothing to store and cannot corrupt a file, but a downloaded original does not carry them.
- Binding to the hash means a restored version (F09), which reuses the same stored file, has the same hash as the earlier one. Its approvals and signatures stay attached to the earlier version number, and the restored version needs its own.
