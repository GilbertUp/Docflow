"""The fixed permission vocabulary. Roles and ACLs are configurable; these codes are not."""

from __future__ import annotations

SYSTEM_PERMISSIONS: dict[str, str] = {
    "security.admin": "Change role definitions and tenant security settings",
    "users.manage": "Create, change and disable users",
    "groups.manage": "Create groups and change membership",
    "departments.manage": "Create and change departments",
    "cabinets.manage": "Create and change cabinets",
    "audit.read": "Read and export the audit trail",
    "integrations.manage": "Manage API keys and identity providers",
    "document_types.manage": "Create document types and change the fields they have",
    "workflows.manage": "Create workflows and change their steps",
    "retention.manage": "Set retention classes and legal holds",
    "forms.manage": "Create forms and see every submission",
}

RESOURCE_PERMISSIONS: dict[str, str] = {
    "read": "See that the item exists, and its metadata",
    "preview": "View content in the browser",
    "download": "Download the original file",
    "create": "Add folders and documents",
    "edit_metadata": "Change names and metadata",
    "version": "Add new versions",
    "move": "Move or copy items",
    "annotate": "Add comments and annotations",
    "share": "Share with others",
    "delete": "Delete or archive items",
    "manage_acl": "Change permissions",
    "dispose": "Approve disposal",
}
