# ADR 0026: Workflows as ordered approval and task steps

**Status:** Accepted on 2026-11-08 (F11, first slice)

## Context
Documents need to be routed: an invoice is checked, approved, then filed. The domain model describes a general graph engine with tokens and timers. That is more than the first scenarios need, and a graph is harder to explain, test and audit than a list.

## Decision
- **A workflow is an ordered list of steps.** Each step is an *approval* (approve, or reject with a reason) or a *task* (complete), for one named person, with an optional number of days to finish. Steps run one after another. Branching, parallel steps, conditions and loops are left for a later slice, so the stored shape can grow into the graph in the domain model without changing what exists.
- **Published versions are immutable.** Changing steps publishes a new version, and a running instance keeps the version it began on. A version identical to the current one is refused. Changing needs the new system right `workflows.manage` (migration 0013 adds it to existing installs). Anyone signed in can list workflows.
- **Steps are tasks (F10).** Activating a step makes a task for the person, with a due date from the days given, and tells them. The task points at its step, and editing it cannot close it. Deciding the step closes the task.
- **Only the person a step is for may decide it,** and someone who cannot read the document gets "not found". Rejection needs a reason, ends the workflow and skips the rest. Completing the last step finishes it. The person who started it is told, unless they decided it.
- **Everyone must be able to read the document,** checked for every step when the workflow starts, so a workflow cannot send a task to someone who cannot see the document. Starting is refused with the names, and refused if the same workflow is already running on the document.
- **Starting.** By hand, needing `edit_metadata` on the document. Or by itself: a workflow can name a document type, and giving a document that type queues a job that starts every matching active workflow, as the person who typed it. A failure to start is audited as `workflow.start_failed`, and never fails the save.
- **Delegation.** A person can hand their new steps to someone else between two dates. The step shows who it was meant for. It only affects steps that begin while it applies, and only if the delegate can read the document.
- **Stopping.** The person who started it, or anyone with `edit_metadata`, can stop it. Open tasks are cancelled.
- **History.** Every transition is a `workflow_event`, shown on the document. Starting, each decision, and the end are also audited.
- **People list.** `GET /people` gives names of active people to any signed-in user, so a step or delegation can be chosen. It gives no emails.
- **Web.** A Workflows page for managing definitions, a Workflow dialog on each document, and a delegation panel on the Tasks page.

## Not in this slice
- Branching, parallel and conditional steps, loops, sub-workflows, and a visual designer.
- Steps for groups or roles, and "any one of these approves".
- Timers, reminders, escalation and business calendars (F13), and signatures bound to an approval (F14).
- Forms that collect data in a step (F12), and automatic actions such as moving or retyping a document.
- Returning a rejected document to an earlier step.

## Consequences
- The list model keeps the engine small enough to reason about, and the version rule means a published workflow can be trusted while it runs.
- Because a delegate is chosen when a step begins, ending a delegation does not take back steps already handed over.
