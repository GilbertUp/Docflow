# ADR 0025: Comments, tasks and in-app notifications

**Status:** Accepted on 2026-11-08 (F10, first slice)

## Context
People work around documents: they discuss them, ask each other to do things, and need to be told. None of this may reveal a document to someone who cannot read it.

## Decision
- **Everything hangs off a document.** Comments, tasks and notifications all point at a document, and everything is visible only to people who may read it.
- **Comments.** Adding one needs `annotate`. A person removes their own, and anyone with `delete` on the document removes any. Mentions are written `@name`, where name is the part of an email before the `@`. A mention only reaches an active user who can read the document, so mentioning someone does not tell them a hidden document exists, and the response lists only who was reached. `document.commented` and `document.comment_removed` are audited.
- **Tasks.** One assignee, a title, a description, a priority (low, normal, high), an optional due time and a status (open, done, cancelled). Creating one, or changing its details or assignee, needs `edit_metadata`. The assignee, the maker and anyone with `edit_metadata` may mark it done or cancelled. The assignee must be an active person who can read the document. Updates use `row_version`, so two people cannot overwrite each other. `task.created` and `task.updated` are audited, with before and after values.
- **Who can be chosen.** `GET /documents/{id}/assignable` lists up to 200 active people who can read the document.
- **Notifications.** `task_assigned`, `task_done` and `mention`, for one person, never for the person who caused the event. They say what happened and nothing else. The list hides any whose document the person can no longer read. Reading is per notification or all at once.
- **My tasks.** `GET /tasks` lists tasks for the caller (or made by them), on documents they can read, open by default, earliest due first.
- **Web.** A Discuss dialog on each document (tasks and comments), a My tasks page and a Notifications page, with an unread count in the top bar that refreshes every minute.

## Not in this slice
- Group assignment, delegation, sub-tasks, recurring tasks and task templates.
- Workflow-created tasks, approvals and SLA clocks (F11, F13, F14).
- Email and other channels, delivery tracking, preferences (F13).
- Annotations on page images, editing a comment, replies and threads, reactions.
- Mentioning by display name, and autocomplete.

## Consequences
- Because notifications are checked against read access when listed, removing someone's access hides their notifications without a clean-up job.
- The unread count includes notifications that are hidden for lack of access, so it can be slightly high.
