# ADR 0016: Folder hierarchy mirrored by the permission anchors

**Status:** Accepted on 2026-11-07 (supersedes the `ltree` path in the domain model)

## Context
F02 needs nested folders to any sensible depth, cheap moves, and permissions that inherit with exceptions. The domain model proposed an `ltree` path per folder. The `ltree` extension is unavailable in the embedded PostgreSQL used for local tests, so any design that needs it can only be tested in CI.

## Decision
- **Adjacency list.** A folder stores its `parent_id`, its `cabinet_id` and its `depth`. Subtrees are read with recursive queries, which need no extension.
- **One anchor per cabinet and folder.** Every cabinet and folder owns an ACL anchor, and the anchor tree mirrors the folder tree. A document points at its folder's anchor, or at its own anchor once it has exceptions.
- **A move re-parents one anchor.** Moving a folder changes one `parent_anchor_id`, so access changes for the whole subtree at once, and no document rows are rewritten. Documents keep their identity.
- **One lock for structure.** Every structural write takes a per-organization advisory lock. A test shows two moves that touch no shared rows can otherwise form a loop (P under S while R goes under Q).
- **Unreadable means not found.** An item the caller cannot read answers 404. Breadcrumbs show unreadable ancestors as "(restricted)" without their names, and counts include only readable documents.
- **The creator owns a new cabinet.** Without that, nobody could use a cabinet they had just made. Disposal rights are not included.
- **Archive instead of delete.** Archived cabinets and folders, and everything below them, accept no new content. Only empty folders and cabinets can be deleted, and the audit record outlives them.
- **Copy copies structure.** A folder copy copies subfolders the caller can read, without permissions or documents. Copying documents needs binaries and versions, which arrive with F03.

## Consequences
- Any structural change creates or moves an anchor, so it bumps the permission epoch. Cached permission trees rebuild after each such change. That is correct but costs more as an organization grows, so it needs measuring in the F22 load tests.
- Structural writes within one organization are serialized. They are rare compared with reads, and the lock is held for one short transaction.
- Maximum depth is a code constant (32) for now. Making it a per-organization setting belongs with F19.
