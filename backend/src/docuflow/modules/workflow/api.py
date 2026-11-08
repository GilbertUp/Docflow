"""Workflow HTTP API: definitions, running a document through them, and delegation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from docuflow.modules.identity.api import Ctx, User_
from docuflow.modules.workflow.schemas import (
    CreateWorkflowRequest,
    DecisionRequest,
    DelegationOut,
    DelegationRequest,
    EventOut,
    InstanceOut,
    NewWorkflowVersionRequest,
    PersonOut,
    StartWorkflowRequest,
    StepModel,
    StepOut,
    UpdateWorkflowRequest,
    WorkflowOut,
)
from docuflow.modules.workflow.service import (
    InstanceView,
    StepInput,
    WorkflowService,
    WorkflowView,
)

router = APIRouter(prefix="/api/v1", tags=["workflow"])


def get_workflow(request: Request) -> WorkflowService:
    service: WorkflowService = request.app.state.workflow
    return service


Flow = Annotated[WorkflowService, Depends(get_workflow)]


def _inputs(steps: list[StepModel]) -> list[StepInput]:
    return [StepInput(s.name, s.kind, s.assignee_id, s.due_days) for s in steps]


def _workflow(view: WorkflowView) -> WorkflowOut:
    return WorkflowOut(
        id=view.workflow.id,
        name=view.workflow.name,
        description=view.workflow.description,
        status=view.workflow.status,
        row_version=view.workflow.row_version,
        version=view.definition.version,
        steps=[
            StepModel(
                name=item["name"],
                kind=item["kind"],
                assignee_id=uuid.UUID(item["assignee_id"]),
                due_days=item.get("due_days"),
            )
            for item in view.definition.steps
        ],
        trigger=dict(view.definition.trigger),
    )


def _instance(view: InstanceView) -> InstanceOut:
    row = view.instance
    return InstanceOut(
        id=row.id,
        document_id=row.document_id,
        workflow_name=view.workflow_name,
        version=view.version,
        status=row.status,
        started_by=row.started_by,
        created_at=row.created_at,
        finished_at=row.finished_at,
        can_cancel=view.can_cancel,
        steps=[
            StepOut(
                id=step.id,
                position=step.position,
                name=step.name,
                kind=step.kind,
                status=step.status,
                assignee_id=step.assignee_id,
                assignee_name=name,
                delegated_from=delegated,
                decided_at=step.decided_at,
                comment=step.comment,
                due_days=step.due_days,
            )
            for step, name, delegated in view.steps
        ],
        events=[
            EventOut(
                kind=e.kind,
                position=e.position,
                actor_id=e.actor_id,
                details=dict(e.details),
                created_at=e.created_at,
            )
            for e in view.events
        ],
    )


@router.get("/workflows")
def list_workflows(
    ctx: Ctx, flow: Flow, who: User_, include_archived: bool = False
) -> list[WorkflowOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_workflow(v) for v in flow.all_workflows(session, include_archived)]


@router.post("/workflows", status_code=201)
def create_workflow(body: CreateWorkflowRequest, ctx: Ctx, flow: Flow, who: User_) -> WorkflowOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _workflow(
            flow.create(
                session, who, body.name, body.description, _inputs(body.steps), body.trigger
            )
        )


@router.get("/workflows/{workflow_id}")
def get_workflow_by_id(workflow_id: uuid.UUID, ctx: Ctx, flow: Flow, who: User_) -> WorkflowOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _workflow(flow.get(session, workflow_id))


@router.post("/workflows/{workflow_id}/versions", status_code=201)
def publish_workflow_version(
    workflow_id: uuid.UUID, body: NewWorkflowVersionRequest, ctx: Ctx, flow: Flow, who: User_
) -> WorkflowOut:
    """Publishes new steps. Work already in progress keeps the version it began on."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _workflow(
            flow.new_version(
                session, who, workflow_id, body.row_version, _inputs(body.steps), body.trigger
            )
        )


@router.patch("/workflows/{workflow_id}")
def update_workflow(
    workflow_id: uuid.UUID, body: UpdateWorkflowRequest, ctx: Ctx, flow: Flow, who: User_
) -> WorkflowOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _workflow(
            flow.update(
                session, who, workflow_id, body.row_version, name=body.name, status=body.status
            )
        )


@router.get("/documents/{document_id}/workflows")
def document_workflows(
    document_id: uuid.UUID, ctx: Ctx, flow: Flow, who: User_
) -> list[InstanceOut]:
    """Every workflow run on this document, newest first, with steps and history."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_instance(v) for v in flow.for_document(session, who, document_id)]


@router.post("/documents/{document_id}/workflows", status_code=201)
def start_workflow(
    document_id: uuid.UUID, body: StartWorkflowRequest, ctx: Ctx, flow: Flow, who: User_
) -> InstanceOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _instance(flow.start(session, who, document_id, body.workflow_id))


@router.post("/workflow-steps/{step_id}/decision")
def decide_step(
    step_id: uuid.UUID, body: DecisionRequest, ctx: Ctx, flow: Flow, who: User_
) -> InstanceOut:
    """Approve, reject (with a reason) or complete the step that is waiting for the caller."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _instance(flow.decide(session, who, step_id, body.decision, body.comment))


@router.post("/workflow-instances/{instance_id}/cancel")
def cancel_instance(instance_id: uuid.UUID, ctx: Ctx, flow: Flow, who: User_) -> InstanceOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _instance(flow.cancel(session, who, instance_id))


@router.get("/delegations")
def list_delegations(ctx: Ctx, flow: Flow, who: User_) -> list[DelegationOut]:
    """The caller's own delegations that have not ended."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            DelegationOut(
                id=row.id,
                to_user_id=row.to_user_id,
                to_name=name,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
            )
            for row, name in flow.delegations(session, who)
        ]


@router.post("/delegations", status_code=201)
def create_delegation(body: DelegationRequest, ctx: Ctx, flow: Flow, who: User_) -> DelegationOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        row = flow.delegate(session, who, body.to_user_id, body.starts_at, body.ends_at)
        name = next((n for r, n in flow.delegations(session, who) if r.id == row.id), "")
        return DelegationOut(
            id=row.id,
            to_user_id=row.to_user_id,
            to_name=name,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
        )


@router.delete("/delegations/{delegation_id}", status_code=204)
def end_delegation(delegation_id: uuid.UUID, ctx: Ctx, flow: Flow, who: User_) -> Response:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        flow.end_delegation(session, who, delegation_id)
    return Response(status_code=204)


@router.get("/people")
def people(ctx: Ctx, flow: Flow, who: User_) -> list[PersonOut]:
    """Names of active people, for choosing who a step or a delegation is for."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [PersonOut(id=p.id, name=p.name) for p in flow.people(session)]
