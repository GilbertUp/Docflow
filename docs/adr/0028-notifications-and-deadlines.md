# ADR 0028: Email notifications, delivery tracking and task deadlines

**Status:** Accepted on 2026-11-08 (F13, first slice)

## Context
F10 and F11 create tasks and in-app notifications. People also need to be told by email when they want that, to be reminded before something is due, and to have someone else told when it is badly late. Email must be tracked, because "did they get it" is the first question when a deadline is missed.

## Decision
- **One way to notify.** Every notification is created through `add_notification`. It records the notification, and queues an email only if that person opted in to that kind. F10 and F11 now use it, so there is a single place for delivery rules.
- **Email is opt-in per kind** (task given, task done, mention, workflow finished, due soon, overdue, escalated), set in the person's own notification preferences. It carries the title and a pointer back to the app, and never document text.
- **Delivery tracking.** Each email is an `email_message` with status (`queued`, `sent`, `failed`), attempts and the last error, and people can see their own recent emails. Sending is a job on the `notify` queue. A mail server error is tried again up to three times in all and then ends as `failed` with the reason. If the server has no mail host set (`DOCUFLOW_SMTP_*` unset), it ends as `failed` at once with "Email is not set up", and the in-app notification is unaffected.
- **Deadlines.** One `sla_policy` per organization: hours before the due time to remind the assignee (default 24, zero for never), and optionally hours after it to tell someone else (a named person, else whoever made the task). Creating a task with a due time, or changing it, sets durable timers (the platform timer table from ADR 0007) for reminder, overdue and escalation. Each timer carries the due time it was made for, so a changed due time retires the old ones without deleting them.
- **Careful when they fire.** A reminder, overdue notice or escalation is sent only while the task is open, the due time is unchanged, the document is active, and the person told can still read it. Escalation is audited as `task.escalated`.
- **Rights.** Anyone signed in can read the policy and manage their own preferences. Changing the policy needs `workflows.manage`, and is audited with before and after.
- **Web.** Email choices and recent emails on the Notifications page, and a Deadlines panel on the Workflows page.

## Not in this slice
- Business calendars, working hours and holidays: deadlines count plain hours.
- Several policies, per-workflow or per-priority policies, and repeated reminders.
- Digests, templates by language, other channels (SMS, chat), and sending to people outside the system.
- A way for administrators to see every person's delivery history, and bounce handling.
- Reminders for tasks created before this change: they have no timers until their due time changes.

## Consequences
- Because timers are rows, deadlines survive restarts and are visible to operators.
- Timers for changed or finished tasks remain as spent rows until a clean-up job exists.
