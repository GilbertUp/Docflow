"""Routing a document through steps of approval and work (F11).

Rules that hold throughout:
- A workflow is a list of steps run in order. Each step is an approval (approve or reject) or a
  task (complete). Publishing changes makes a new immutable version, and an instance keeps the
  version it began on, so editing a workflow never changes work already in progress.
- Each active step is a task for one person (F10), so it shows in their tasks and notifications.
  It is finished by deciding the step, never by editing the task.
- Only the person a step is for may decide it. A delegation sends new steps to someone else for a
  period, and the step records who it was meant for.
- A rejection ends the workflow and needs a reason. Remaining steps are skipped.
- Everyone on a workflow must be able to read the document, checked when it starts.
- Every transition is recorded in the instance's history, and the main ones are audited.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docuflow.modules.collaboration.models import Task
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.models import User
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.indexing.models import DocumentType
from docuflow.modules.notifications.service import add_notification, schedule_task_timers
from docuflow.modules.repository.models import Document
from docuflow.modules.repository.service import RepositoryService
from docuflow.modules.signatures.service import record_approval
from docuflow.modules.workflow.models import (
    Delegation,
    Workflow,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowInstance,
    WorkflowStep,
)
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import Database, utcnow
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound, ValidationFailed
from docuflow.platform.jobs import JobContext

MANAGE = "workflows.manage"
JOB_TYPE = "workflow.document_typed"
MAX_STEPS = 20
MAX_NAME = 120
MAX_DUE_DAYS = 365
MAX_COMMENT = 2000
KINDS = ("approval", "task")
DECISIONS = {"approval": ("approve", "reject"), "task": ("complete",)}
EVENT_TYPED = "document.typed"


@dataclass(frozen=True)
class StepInput:
    name: str
    kind: str
    assignee_id: uuid.UUID
    due_days: int | None = None


@dataclass(frozen=True)
class Person:
    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class WorkflowView:
    workflow: Workflow
    definition: WorkflowDefinition


@dataclass(frozen=True)
class InstanceView:
    instance: WorkflowInstance
    workflow_name: str
    version: int
    steps: list[tuple[WorkflowStep, str, str | None]]  # step, assignee name, delegated-from name
    events: list[WorkflowEvent]
    can_cancel: bool


def trigger_for(session: Session, raw: dict[str, Any]) -> dict[str, Any]:
    """Checks a trigger and returns it in its stored form."""
    if not raw:
        return {}
    if set(raw) - {"event", "document_type_id"} or raw.get("event") != EVENT_TYPED:
        raise DomainError("A trigger is empty, or starts when a document is given a type.")
    try:
        type_id = uuid.UUID(str(raw.get("document_type_id")))
    except ValueError as exc:
        raise DomainError("Choose the document type that starts this workflow.") from exc
    if session.get(DocumentType, type_id) is None:
        raise DomainError("That document type does not exist.")
    return {"event": EVENT_TYPED, "document_type_id": str(type_id)}


class WorkflowService:
    def __init__(self, audit: AuditLog, policy: PolicyService, repo: RepositoryService) -> None:
        self._audit = audit
        self._policy = policy
        self._repo = repo

    # Helpers

    def _record(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        action: str,
        object_type: str,
        object_id: uuid.UUID,
        details: dict[str, object],
    ) -> None:
        self._audit.record(
            session,
            tenant_id,
            action,
            actor=AuditActor(actor_id, "user") if actor_id else AuditActor(),
            object_type=object_type,
            object_id=str(object_id),
            details=details,
        )

    def _require_manage(self, session: Session, principal: Principal) -> None:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        if MANAGE not in held:
            raise Forbidden("You do not have permission to do that.", details={"required": MANAGE})

    def _can_read(
        self, session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, document: Document
    ) -> bool:
        tree = self._policy.tree(session, tenant_id, user_id)
        return tree.decide(document.acl_anchor_id, "read").allowed

    @staticmethod
    def _event(
        session: Session,
        instance: WorkflowInstance,
        kind: str,
        actor_id: uuid.UUID | None,
        position: int | None = None,
        **details: object,
    ) -> None:
        session.add(
            WorkflowEvent(
                tenant_id=instance.tenant_id,
                instance_id=instance.id,
                position=position,
                kind=kind,
                actor_id=actor_id,
                details=dict(details),
            )
        )

    def people(self, session: Session) -> list[Person]:
        rows = session.scalars(
            select(User)
            .where(User.status == "active", User.kind == "person")
            .order_by(User.display_name, User.id)
            .limit(500)
        )
        return [Person(u.id, u.display_name) for u in rows]

    # Definitions

    @staticmethod
    def _check_steps(session: Session, steps: list[StepInput]) -> list[dict[str, Any]]:
        if not steps:
            raise DomainError("A workflow needs at least one step.")
        if len(steps) > MAX_STEPS:
            raise DomainError(f"A workflow can have at most {MAX_STEPS} steps.")
        problems: list[dict[str, str]] = []
        stored: list[dict[str, Any]] = []
        for number, step in enumerate(steps, start=1):
            label = f"step {number}"
            name = step.name.strip()
            if not name or len(name) > MAX_NAME:
                problems.append(
                    {"field": label, "message": f"Give it a name up to {MAX_NAME} characters."}
                )
            if step.kind not in KINDS:
                problems.append({"field": label, "message": "The kind must be approval or task."})
            if step.due_days is not None and not 0 <= step.due_days <= MAX_DUE_DAYS:
                problems.append(
                    {"field": label, "message": f"Days to finish must be 0 to {MAX_DUE_DAYS}."}
                )
            user = session.get(User, step.assignee_id)
            if user is None or user.status != "active" or user.kind != "person":
                problems.append({"field": label, "message": "Choose an active person."})
            stored.append(
                {
                    "name": name,
                    "kind": step.kind,
                    "assignee_id": str(step.assignee_id),
                    "due_days": step.due_days,
                }
            )
        if problems:
            raise ValidationFailed("Some steps are not valid.", problems)
        return stored

    def create(
        self,
        session: Session,
        principal: Principal,
        name: str,
        description: str,
        steps: list[StepInput],
        trigger: dict[str, Any],
    ) -> WorkflowView:
        self._require_manage(session, principal)
        name = name.strip()
        if not name or len(name) > MAX_NAME:
            raise DomainError(f"Names must be between 1 and {MAX_NAME} characters.")
        stored = self._check_steps(session, steps)
        checked = trigger_for(session, trigger)
        workflow = Workflow(
            tenant_id=principal.tenant_id,
            name=name,
            description=description.strip()[:1000],
            created_by=principal.user_id,
        )
        session.add(workflow)
        try:
            session.flush()
        except IntegrityError as exc:
            raise Conflict("A workflow with that name already exists.") from exc
        definition = WorkflowDefinition(
            tenant_id=principal.tenant_id,
            workflow_id=workflow.id,
            version=1,
            steps=stored,
            trigger=checked,
            created_by=principal.user_id,
        )
        session.add(definition)
        session.flush()
        self._record(
            session,
            principal.tenant_id,
            principal.user_id,
            "workflow.created",
            "workflow",
            workflow.id,
            {"name": name, "steps": len(stored)},
        )
        return WorkflowView(workflow, definition)

    def _latest(self, session: Session, workflow_id: uuid.UUID) -> WorkflowDefinition:
        definition = session.scalar(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.workflow_id == workflow_id)
            .order_by(WorkflowDefinition.version.desc())
            .limit(1)
        )
        if definition is None:
            raise NotFound("Workflow not found.")
        return definition

    def all_workflows(self, session: Session, include_archived: bool = False) -> list[WorkflowView]:
        query = select(Workflow).order_by(Workflow.name)
        if not include_archived:
            query = query.where(Workflow.status == "active")
        return [WorkflowView(row, self._latest(session, row.id)) for row in session.scalars(query)]

    def get(self, session: Session, workflow_id: uuid.UUID) -> WorkflowView:
        workflow = session.get(Workflow, workflow_id)
        if workflow is None:
            raise NotFound("Workflow not found.")
        return WorkflowView(workflow, self._latest(session, workflow.id))

    def new_version(
        self,
        session: Session,
        principal: Principal,
        workflow_id: uuid.UUID,
        row_version: int,
        steps: list[StepInput],
        trigger: dict[str, Any],
    ) -> WorkflowView:
        self._require_manage(session, principal)
        workflow = session.scalar(
            select(Workflow).where(Workflow.id == workflow_id).with_for_update()
        )
        if workflow is None:
            raise NotFound("Workflow not found.")
        if workflow.row_version != row_version:
            raise Conflict("This workflow was changed by someone else. Reload and try again.")
        stored = self._check_steps(session, steps)
        checked = trigger_for(session, trigger)
        latest = self._latest(session, workflow.id)
        if latest.steps == stored and latest.trigger == checked:
            raise Conflict("The steps are the same as the current version.")
        definition = WorkflowDefinition(
            tenant_id=principal.tenant_id,
            workflow_id=workflow.id,
            version=latest.version + 1,
            steps=stored,
            trigger=checked,
            created_by=principal.user_id,
        )
        session.add(definition)
        workflow.row_version += 1
        session.flush()
        self._record(
            session,
            principal.tenant_id,
            principal.user_id,
            "workflow.version_published",
            "workflow",
            workflow.id,
            {"version": definition.version, "steps": len(stored)},
        )
        return WorkflowView(workflow, definition)

    def update(
        self,
        session: Session,
        principal: Principal,
        workflow_id: uuid.UUID,
        row_version: int,
        *,
        name: str | None = None,
        status: str | None = None,
    ) -> WorkflowView:
        self._require_manage(session, principal)
        workflow = session.scalar(
            select(Workflow).where(Workflow.id == workflow_id).with_for_update()
        )
        if workflow is None:
            raise NotFound("Workflow not found.")
        if workflow.row_version != row_version:
            raise Conflict("This workflow was changed by someone else. Reload and try again.")
        changes: dict[str, object] = {}
        if name is not None and name.strip() != workflow.name:
            clean = name.strip()
            if not clean or len(clean) > MAX_NAME:
                raise DomainError(f"Names must be between 1 and {MAX_NAME} characters.")
            changes["name"] = [workflow.name, clean]
            workflow.name = clean
        if status is not None and status != workflow.status:
            if status not in ("active", "archived"):
                raise DomainError("The status must be active or archived.")
            changes["status"] = [workflow.status, status]
            workflow.status = status
        if changes:
            workflow.row_version += 1
            try:
                session.flush()
            except IntegrityError as exc:
                raise Conflict("A workflow with that name already exists.") from exc
            self._record(
                session,
                principal.tenant_id,
                principal.user_id,
                "workflow.updated",
                "workflow",
                workflow.id,
                {"changes": changes},
            )
        return WorkflowView(workflow, self._latest(session, workflow.id))

    # Running

    def _delegate_of(
        self, session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, document: Document
    ) -> uuid.UUID | None:
        now = utcnow()
        row = session.scalar(
            select(Delegation)
            .where(
                Delegation.from_user_id == user_id,
                Delegation.starts_at <= now,
                Delegation.ends_at > now,
            )
            .order_by(Delegation.created_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        to = session.get(User, row.to_user_id)
        if (
            to is None
            or to.status != "active"
            or not self._can_read(session, tenant_id, to.id, document)
        ):
            return None
        return to.id

    def _activate(
        self, session: Session, instance: WorkflowInstance, document: Document, step: WorkflowStep
    ) -> None:
        owner = step.assignee_id
        delegate = self._delegate_of(session, instance.tenant_id, owner, document)
        if delegate is not None:
            step.delegated_from_id = owner
            step.assignee_id = delegate
        step.status = "active"
        verb = "Approve" if step.kind == "approval" else "Do"
        task = Task(
            tenant_id=instance.tenant_id,
            document_id=document.id,
            title=f"{verb}: {step.name}"[:200],
            description=f"Step {step.position + 1} of a workflow on {document.title}.",
            assignee_id=step.assignee_id,
            created_by=instance.started_by,
            due_at=utcnow() + timedelta(days=step.due_days) if step.due_days is not None else None,
            workflow_step_id=step.id,
        )
        session.add(task)
        session.flush()
        step.task_id = task.id
        schedule_task_timers(session, instance.tenant_id, task)
        if step.assignee_id != instance.started_by:
            add_notification(
                session,
                tenant_id=instance.tenant_id,
                user_id=step.assignee_id,
                kind="task_assigned",
                title=f"Task for you: {task.title}",
                document_id=document.id,
                ref_id=task.id,
                actor_id=instance.started_by,
            )
        self._event(
            session,
            instance,
            "step_activated",
            instance.started_by,
            step.position,
            assignee=str(step.assignee_id),
            delegated_from=str(owner) if delegate is not None else None,
        )

    def _start(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        starter: uuid.UUID,
        document: Document,
        workflow: Workflow,
        *,
        automatic: bool,
    ) -> WorkflowInstance:
        if workflow.status != "active":
            raise Conflict("That workflow is archived.")
        definition = self._latest(session, workflow.id)
        running = session.scalar(
            select(WorkflowInstance.id)
            .join(WorkflowDefinition, WorkflowDefinition.id == WorkflowInstance.definition_id)
            .where(
                WorkflowInstance.document_id == document.id,
                WorkflowInstance.status == "running",
                WorkflowDefinition.workflow_id == workflow.id,
            )
        )
        if running is not None:
            raise Conflict("That workflow is already running on this document.")
        unreadable = []
        for item in definition.steps:
            user = session.get(User, uuid.UUID(item["assignee_id"]))
            if user is None or user.status != "active":
                raise Conflict(f"The person for “{item['name']}” is no longer active.")
            if not self._can_read(session, tenant_id, user.id, document):
                unreadable.append(user.display_name)
        if unreadable:
            raise Conflict(
                "These people cannot read the document: " + ", ".join(sorted(set(unreadable))) + "."
            )
        instance = WorkflowInstance(
            tenant_id=tenant_id,
            definition_id=definition.id,
            document_id=document.id,
            started_by=starter,
        )
        session.add(instance)
        session.flush()
        steps: list[WorkflowStep] = []
        for position, item in enumerate(definition.steps):
            step = WorkflowStep(
                tenant_id=tenant_id,
                instance_id=instance.id,
                position=position,
                name=item["name"],
                kind=item["kind"],
                assignee_id=uuid.UUID(item["assignee_id"]),
                due_days=item.get("due_days"),
            )
            session.add(step)
            steps.append(step)
        session.flush()
        self._event(
            session,
            instance,
            "started",
            starter,
            None,
            workflow=workflow.name,
            version=definition.version,
            automatic=automatic,
        )
        self._activate(session, instance, document, steps[0])
        self._record(
            session,
            tenant_id,
            starter,
            "workflow.started",
            "document",
            document.id,
            {
                "workflow": workflow.name,
                "version": definition.version,
                "automatic": automatic,
                "instance": str(instance.id),
            },
        )
        return instance

    def start(
        self,
        session: Session,
        principal: Principal,
        document_id: uuid.UUID,
        workflow_id: uuid.UUID,
    ) -> InstanceView:
        document = self._repo.document_for_change(
            session, principal, document_id, "edit_metadata", lock=True
        )
        workflow = session.get(Workflow, workflow_id)
        if workflow is None:
            raise NotFound("Workflow not found.")
        instance = self._start(
            session, principal.tenant_id, principal.user_id, document, workflow, automatic=False
        )
        return self._view(session, principal, instance)

    def _finish(
        self,
        session: Session,
        instance: WorkflowInstance,
        document: Document,
        status: str,
        actor_id: uuid.UUID,
        message: str,
    ) -> None:
        instance.status = status
        instance.finished_at = utcnow()
        instance.row_version += 1
        self._event(session, instance, status, actor_id, None)
        if instance.started_by and instance.started_by != actor_id:
            add_notification(
                session,
                tenant_id=instance.tenant_id,
                user_id=instance.started_by,
                kind="workflow_finished",
                title=f"{message}: {document.title}",
                document_id=document.id,
                ref_id=instance.id,
                actor_id=actor_id,
            )
        self._record(
            session,
            instance.tenant_id,
            actor_id,
            f"workflow.{status}",
            "document",
            document.id,
            {"instance": str(instance.id)},
        )

    def decide(
        self,
        session: Session,
        principal: Principal,
        step_id: uuid.UUID,
        decision: str,
        comment: str,
    ) -> InstanceView:
        step = session.scalar(
            select(WorkflowStep).where(WorkflowStep.id == step_id).with_for_update()
        )
        instance = session.get(WorkflowInstance, step.instance_id) if step else None
        document = session.get(Document, instance.document_id) if instance else None
        if (
            step is None
            or instance is None
            or document is None
            or step.assignee_id != principal.user_id
        ):
            raise NotFound("Step not found.")
        if step.status != "active" or instance.status != "running":
            raise Conflict("That step is not waiting for a decision.")
        if decision not in DECISIONS[step.kind]:
            raise DomainError("The decision must be " + " or ".join(DECISIONS[step.kind]) + ".")
        comment = comment.strip()
        if len(comment) > MAX_COMMENT:
            raise DomainError(f"A comment can be up to {MAX_COMMENT} characters.")
        if decision == "reject" and not comment:
            raise DomainError("Say why it is being rejected.")
        if not self._can_read(session, principal.tenant_id, principal.user_id, document):
            raise NotFound("Step not found.")
        now = utcnow()
        step.status = {"approve": "approved", "reject": "rejected", "complete": "done"}[decision]
        step.decided_by = principal.user_id
        step.decided_at = now
        step.comment = comment or None
        if step.kind == "approval":
            record_approval(
                session,
                principal.tenant_id,
                document,
                principal.user_id,
                decision,
                comment or None,
                step.id,
            )
        task = session.get(Task, step.task_id) if step.task_id else None
        if task is not None:
            task.status = "done"
            task.completed_at = now
            task.completed_by = principal.user_id
            task.row_version += 1
        self._event(
            session,
            instance,
            "decided",
            principal.user_id,
            step.position,
            decision=decision,
            comment=comment[:200],
        )
        self._record(
            session,
            principal.tenant_id,
            principal.user_id,
            "workflow.step_decided",
            "document",
            document.id,
            {"instance": str(instance.id), "step": step.name, "decision": decision},
        )
        later = list(
            session.scalars(
                select(WorkflowStep)
                .where(
                    WorkflowStep.instance_id == instance.id, WorkflowStep.position > step.position
                )
                .order_by(WorkflowStep.position)
            )
        )
        if decision == "reject":
            for item in later:
                item.status = "skipped"
            self._finish(session, instance, document, "rejected", principal.user_id, "Rejected")
        elif later:
            self._activate(session, instance, document, later[0])
            instance.row_version += 1
        else:
            self._finish(session, instance, document, "completed", principal.user_id, "Approved")
        session.flush()
        return self._view(session, principal, instance)

    def cancel(
        self, session: Session, principal: Principal, instance_id: uuid.UUID
    ) -> InstanceView:
        instance = session.scalar(
            select(WorkflowInstance).where(WorkflowInstance.id == instance_id).with_for_update()
        )
        document = session.get(Document, instance.document_id) if instance else None
        if (
            instance is None
            or document is None
            or not self._can_read(session, principal.tenant_id, principal.user_id, document)
        ):
            raise NotFound("Workflow instance not found.")
        if instance.status != "running":
            raise Conflict("That workflow has already finished.")
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        if (
            instance.started_by != principal.user_id
            and not tree.decide(document.acl_anchor_id, "edit_metadata").allowed
        ):
            raise Forbidden(
                "Only the person who started it, or someone who can edit the document, can stop it."
            )
        for step in session.scalars(
            select(WorkflowStep).where(
                WorkflowStep.instance_id == instance.id,
                WorkflowStep.status.in_(("pending", "active")),
            )
        ):
            if step.task_id:
                task = session.get(Task, step.task_id)
                if task is not None and task.status == "open":
                    task.status = "cancelled"
                    task.completed_at = utcnow()
                    task.completed_by = principal.user_id
                    task.row_version += 1
            step.status = "skipped"
        self._finish(session, instance, document, "cancelled", principal.user_id, "Stopped")
        session.flush()
        return self._view(session, principal, instance)

    # Reading

    def _view(
        self, session: Session, principal: Principal, instance: WorkflowInstance
    ) -> InstanceView:
        definition = session.get(WorkflowDefinition, instance.definition_id)
        assert definition is not None
        workflow = session.get(Workflow, definition.workflow_id)
        assert workflow is not None
        steps = list(
            session.scalars(
                select(WorkflowStep)
                .where(WorkflowStep.instance_id == instance.id)
                .order_by(WorkflowStep.position)
            )
        )
        ids = {s.assignee_id for s in steps} | {
            s.delegated_from_id for s in steps if s.delegated_from_id
        }
        names = {
            u.id: u.display_name for u in session.scalars(select(User).where(User.id.in_(ids)))
        }
        events = list(
            session.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.instance_id == instance.id)
                .order_by(WorkflowEvent.created_at, WorkflowEvent.id)
            )
        )
        document = session.get(Document, instance.document_id)
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        can_cancel = instance.status == "running" and (
            instance.started_by == principal.user_id
            or (
                document is not None
                and tree.decide(document.acl_anchor_id, "edit_metadata").allowed
            )
        )
        return InstanceView(
            instance,
            workflow.name,
            definition.version,
            [
                (
                    s,
                    names.get(s.assignee_id, "Unknown"),
                    names.get(s.delegated_from_id) if s.delegated_from_id else None,
                )
                for s in steps
            ],
            events,
            can_cancel,
        )

    def for_document(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> list[InstanceView]:
        document = self._repo.get_document(session, principal, document_id)
        rows = session.scalars(
            select(WorkflowInstance)
            .where(WorkflowInstance.document_id == document.id)
            .order_by(WorkflowInstance.created_at.desc(), WorkflowInstance.id)
            .limit(50)
        )
        return [self._view(session, principal, row) for row in rows]

    # Delegations

    def delegate(
        self,
        session: Session,
        principal: Principal,
        to_user_id: uuid.UUID,
        starts_at: datetime,
        ends_at: datetime,
    ) -> Delegation:
        if starts_at >= ends_at:
            raise DomainError("The end must be after the start.")
        if to_user_id == principal.user_id:
            raise DomainError("You cannot delegate to yourself.")
        user = session.get(User, to_user_id)
        if user is None or user.status != "active" or user.kind != "person":
            raise DomainError("Choose an active person.")
        row = Delegation(
            tenant_id=principal.tenant_id,
            from_user_id=principal.user_id,
            to_user_id=to_user_id,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        session.add(row)
        session.flush()
        self._record(
            session,
            principal.tenant_id,
            principal.user_id,
            "delegation.created",
            "delegation",
            row.id,
            {"to": str(to_user_id), "from": starts_at.isoformat(), "until": ends_at.isoformat()},
        )
        return row

    def delegations(self, session: Session, principal: Principal) -> list[tuple[Delegation, str]]:
        rows = list(
            session.scalars(
                select(Delegation)
                .where(Delegation.from_user_id == principal.user_id, Delegation.ends_at > utcnow())
                .order_by(Delegation.starts_at)
            )
        )
        names = (
            {
                u.id: u.display_name
                for u in session.scalars(
                    select(User).where(User.id.in_({r.to_user_id for r in rows}))
                )
            }
            if rows
            else {}
        )
        return [(row, names.get(row.to_user_id, "Unknown")) for row in rows]

    def end_delegation(
        self, session: Session, principal: Principal, delegation_id: uuid.UUID
    ) -> None:
        row = session.get(Delegation, delegation_id)
        if row is None or row.from_user_id != principal.user_id:
            raise NotFound("Delegation not found.")
        session.delete(row)
        self._record(
            session,
            principal.tenant_id,
            principal.user_id,
            "delegation.removed",
            "delegation",
            delegation_id,
            {"to": str(row.to_user_id)},
        )

    # Starting by itself (called by the worker)

    def run(self, db: Database, ctx: JobContext) -> None:
        """Starts the workflows that begin when a document is given a type."""
        document_id = uuid.UUID(ctx.payload["document_id"])
        type_id = ctx.payload["type_id"]
        starter = uuid.UUID(ctx.payload["user_id"])
        with db.tenant_session(ctx.tenant_id) as session:
            document = session.scalar(
                select(Document).where(Document.id == document_id).with_for_update()
            )
            if document is None or document.status != "active":
                return
            if str(document.document_type_id) != type_id:
                return  # typed again since, so this trigger no longer applies
            for workflow in session.scalars(select(Workflow).where(Workflow.status == "active")):
                latest = self._latest(session, workflow.id)
                if latest.trigger.get("event") != EVENT_TYPED:
                    continue
                if latest.trigger.get("document_type_id") != type_id:
                    continue
                try:
                    with session.begin_nested():
                        self._start(
                            session, ctx.tenant_id, starter, document, workflow, automatic=True
                        )
                except (Conflict, DomainError) as exc:
                    self._record(
                        session,
                        ctx.tenant_id,
                        starter,
                        "workflow.start_failed",
                        "document",
                        document.id,
                        {"workflow": workflow.name, "reason": exc.message},
                    )
