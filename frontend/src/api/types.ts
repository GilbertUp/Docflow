// Friendly names for the generated API types. Run `npm run gen:api` after backend changes.
import type { components } from "./schema";

type S = components["schemas"];

export type User = S["UserOut"];
export type Me = S["MeResponse"];
export type LoginResult = S["LoginResponse"];
export type Session = S["SessionOut"];
export type Group = S["GroupOut"];
export type Role = S["RoleOut"];
export type RoleHolders = S["RoleHoldersOut"];
export type Department = S["DepartmentOut"];
export type ApiKey = S["ApiKeyOut"];
export type ApiKeyCreated = S["ApiKeyCreated"];
export type PermissionInfo = S["PermissionOut"];
export type AclEntry = S["AclEntryModel"];
export type AclResponse = S["AclResponse"];
export type MfaSetup = S["MfaSetupResponse"];
export type Cabinet = S["CabinetOut"];
export type CabinetDetail = S["CabinetDetail"];
export type Folder = S["FolderOut"];
export type FolderDetail = S["FolderDetail"];
export type DocumentRow = S["DocumentOut"];
export type UploadResult = S["UploadResult"];
export type VersionRow = S["VersionOut"];
export type ImportResult = S["ImportOut"];
export type TextResult = S["TextOut"];
export type DocumentTypeRow = S["TypeOut"];
export type FieldDefinition = S["FieldModel"];
export type IndexField = S["IndexFieldOut"];
export type IndexResult = S["IndexOut"];
export type PreviewInfo = S["PreviewOut"];
export type SearchResult = S["SearchOut"];
export type DocumentLink = S["LinkOut"];
export type LinkType = S["LinkTypeOut"];
export type CommentRow = S["CommentOut"];
export type Person = S["PersonOut"];
export type TaskRow = S["TaskOut"];
export type NotificationList = S["NotificationsOut"];
export type WorkflowRow = S["WorkflowOut"];
export type WorkflowInstance = S["InstanceOut"];
export type PersonRow = S["PersonOut"];
export type DelegationRow = S["DelegationOut"];
export type FormRow = S["FormOut"];
export type FormFieldRow = S["FormFieldModel"];
export type SubmissionRow = S["SubmissionOut"];
export type PreferenceRow = S["PreferenceOut"];
export type SlaPolicyRow = S["SlaOut"];
export type DeliveryRow = S["DeliveryOut"];
export type ApprovalRow = S["ApprovalOut"];
export type SignatureRow = S["SignatureOut"];
export type StampRow = S["StampOut"];
export type AppliedStamp = S["AppliedStampOut"];
export type RetentionClassRow = S["ClassOut"];
export type DocumentRetention = S["RetentionOut"];
export type LegalHoldRow = S["HoldOut"];
export type DisposalCandidate = S["CandidateOut"];
export type DisposalRecord = S["DisposalOut"];
export type AuditEvents = S["AuditEventsOut"];
export type AuditEvent = S["AuditEventOut"];
export type AuditVerification = S["VerifyOut"];
export type CompressionRun = S["DerivativeOut"];
export type AssistantAnswer = S["AssistantAnswerOut"];
export type AssistantSettings = S["AssistantSettingsOut"];
export type AdminAnalytics = S["AnalyticsOut"];
export type AdminHealth = S["HealthOut"];
export type AdminJob = S["JobOut"];
export type JobSummary = S["JobSummaryOut"];
export type WebhookRow = S["WebhookOut"];
export type WebhookCreatedRow = S["WebhookCreated"];
export type WebhookDeliveryRow = S["WebhookDeliveryOut"];
export type WebhookEventType = S["WebhookEventTypeOut"];
export type Suggestions = S["SuggestionsOut"];
export type SuggestedField = S["SuggestedFieldOut"];
export type LanguageList = S["LanguagesOut"];
export type VersionResult = S["VersionResult"];
export type Children = S["ChildrenOut"];
export type Stats = S["StatsOut"];
export type Crumb = S["CrumbOut"];
export type CopyResult = S["CopyResult"];

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// The inspector's response is free-form on the server, so it is described here.
export interface EffectivePermission {
  allowed: boolean;
  reason: string;
  source_anchor_id: string | null;
}

export type Destination = { cabinet_id: string } | { folder_id: string };

// Mirrors the server's fixed vocabulary of resource permissions.
export const RESOURCE_PERMISSIONS = [
  ["read", "See that it exists, and its details"],
  ["preview", "View content in the browser"],
  ["download", "Download the original file"],
  ["create", "Add folders and documents"],
  ["edit_metadata", "Rename and edit details"],
  ["version", "Add new versions"],
  ["move", "Move or copy"],
  ["annotate", "Comment and annotate"],
  ["share", "Share with others"],
  ["delete", "Archive or delete"],
  ["manage_acl", "Change who has access"],
  ["dispose", "Approve disposal"],
] as const;

export const SYSTEM_PERMISSIONS = [
  ["security.admin", "Change roles and security settings"],
  ["users.manage", "Create and change users"],
  ["groups.manage", "Create groups and change membership"],
  ["departments.manage", "Create and change departments"],
  ["cabinets.manage", "Create and change cabinets"],
  ["audit.read", "Read and export the audit trail"],
  ["integrations.manage", "Manage API keys and identity providers"],
  ["document_types.manage", "Create document types and change their fields"],
  ["workflows.manage", "Create workflows and change their steps"],
  ["admin.operate", "See usage and the job monitor, and retry failed jobs"],
  ["retention.manage", "Set retention classes and legal holds"],
  ["forms.manage", "Create forms and see every submission"],
] as const;
