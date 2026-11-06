# ADR 0004: ACL inheritance resolved through anchors

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
The spec needs permissions at organization, cabinet, folder, document type and document level, allow and deny inheritance, exceptions, an effective-permission view, and immediate effect when a role changes. Search and AI must filter *before* ranking, so a per-document check after the query is not enough.

## Decision

### Two kinds of permission
- **System permissions** (manage users, view audit and so on) belong to configurable roles.
- **Resource permissions** are ACL entries: `read`, `preview`, `download`, `create`, `edit_metadata`, `version`, `move`, `annotate`, `share`, `delete`, `manage_acl`, `dispose`.

`read`, `preview` and `download` are separate, so a user can view a document without being able to take a copy.

### Resolution rules
1. Each permission is resolved independently along tenant, cabinet, folder path, then document.
2. The **nearest level with a matching entry wins**, which is how exceptions work.
3. At the same level, **deny beats allow**.
4. **Document-type entries only restrict.** A type-level deny removes that permission everywhere and cannot be overridden lower down. Type-level allow grants nothing by itself.
5. No matching entry means no access.

### Efficient filtering
Only nodes with explicit ACL entries become **anchors**. Every folder and document stores `acl_anchor_id`, its nearest anchor. For each request the policy service computes the anchors the user can read, then filters by:

```sql
d.acl_anchor_id = ANY(:readable_anchors) AND d.document_type_id <> ALL(:denied_types)
```

The same predicate is denormalised onto search chunks and applied to every path: search, OCR text, previews, thumbnails, derivatives, embeddings and AI retrieval.

### Immediate effect
Any ACL, role or membership change bumps a tenant `permission_epoch` in the same transaction. Cached anchor sets are keyed by that epoch, so old entries can never be used.

### Escalation prevention
No one can grant a permission they do not hold. Only a role with the `security.admin` system permission may change role definitions, and a tenant must always keep one such user.

## Consequences
- Filtering stays one indexed predicate, regardless of how many documents exist.
- Moving a folder recomputes anchors for its subtree in one transaction. Large moves run as a locked job, and a concurrent rename receives a conflict.
- Optional ABAC attaches later as extra predicates, without changing the ACL model.
- A single permission-matrix test suite is generated from the route list, so an unprotected new endpoint fails CI.
