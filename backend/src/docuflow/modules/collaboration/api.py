"""Collaboration HTTP API: comments, tasks and notifications."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from docuflow.modules.collaboration.schemas import (
    AddCommentRequest,
    CommentOut,
    CreateTaskRequest,
    NotificationOut,
    NotificationsOut,
    PersonOut,
    TaskOut,
    UpdateTaskRequest,
)
from docuflow.modules.collaboration.service import CollaborationService, CommentView, TaskView
from docuflow.modules.identity.api import Ctx, User_

router = APIRouter(prefix="/api/v1", tags=["collaboration"])


def get_collaboration(request: Request) -> CollaborationService:
    service: CollaborationService = request.app.state.collaboration
    return service


Collab = Annotated[CollaborationService, Depends(get_collaboration)]


def _comment(view: CommentView) -> CommentOut:
    row = view.comment
    return CommentOut(
        id=row.id,
        author_id=row.author_id,
        author_name=view.author_name,
        body=row.body,
        mentioned=view.mentioned,
        created_at=row.created_at,
        can_delete=view.can_delete,
    )


def _task(view: TaskView) -> TaskOut:
    row = view.task
    return TaskOut(
        id=row.id,
        document_id=row.document_id,
        document_title=view.document_title,
        title=row.title,
        description=row.description,
        status=row.status,
        priority=row.priority,
        assignee_id=row.assignee_id,
        assignee_name=view.assignee_name,
        due_at=row.due_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        row_version=row.row_version,
        can_change=view.can_change,
    )


@router.get("/documents/{document_id}/comments")
def list_comments(document_id: uuid.UUID, ctx: Ctx, collab: Collab, who: User_) -> list[CommentOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_comment(item) for item in collab.comments(session, who, document_id)]


@router.post("/documents/{document_id}/comments", status_code=201)
def add_comment(
    document_id: uuid.UUID, body: AddCommentRequest, ctx: Ctx, collab: Collab, who: User_
) -> CommentOut:
    """Adds a comment. Write @name (the part of an email before the @) to mention someone."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _comment(collab.add_comment(session, who, document_id, body.body))


@router.delete("/documents/{document_id}/comments/{comment_id}", status_code=204)
def delete_comment(
    document_id: uuid.UUID, comment_id: uuid.UUID, ctx: Ctx, collab: Collab, who: User_
) -> Response:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        collab.delete_comment(session, who, document_id, comment_id)
    return Response(status_code=204)


@router.get("/documents/{document_id}/assignable")
def assignable(document_id: uuid.UUID, ctx: Ctx, collab: Collab, who: User_) -> list[PersonOut]:
    """People who can read the document, and so can be given a task on it."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            PersonOut(id=p.id, name=p.name) for p in collab.assignable(session, who, document_id)
        ]


@router.get("/documents/{document_id}/tasks")
def document_tasks(
    document_id: uuid.UUID,
    ctx: Ctx,
    collab: Collab,
    who: User_,
    status: str | None = None,
) -> list[TaskOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            _task(item)
            for item in collab.tasks(session, who, document_id=document_id, status=status)
        ]


@router.post("/documents/{document_id}/tasks", status_code=201)
def create_task(
    document_id: uuid.UUID, body: CreateTaskRequest, ctx: Ctx, collab: Collab, who: User_
) -> TaskOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _task(
            collab.create_task(
                session,
                who,
                document_id,
                title=body.title,
                description=body.description,
                assignee_id=body.assignee_id,
                priority=body.priority,
                due_at=body.due_at,
            )
        )


@router.get("/tasks")
def my_tasks(
    ctx: Ctx,
    collab: Collab,
    who: User_,
    status: str | None = "open",
    mine: bool = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> list[TaskOut]:
    """Tasks for the caller (mine) or made by them, on documents they can read."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            _task(item)
            for item in collab.tasks(
                session, who, mine=mine, status=status, limit=limit, offset=offset
            )
        ]


@router.patch("/tasks/{task_id}")
def update_task(
    task_id: uuid.UUID, body: UpdateTaskRequest, ctx: Ctx, collab: Collab, who: User_
) -> TaskOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _task(
            collab.update_task(
                session,
                who,
                task_id,
                body.row_version,
                status=body.status,
                title=body.title,
                priority=body.priority,
                assignee_id=body.assignee_id,
                due_at=body.due_at,
                clear_due=body.clear_due,
            )
        )


@router.get("/notifications")
def list_notifications(
    ctx: Ctx,
    collab: Collab,
    who: User_,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> NotificationsOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        rows, unread = collab.notifications(
            session, who, unread_only=unread_only, limit=limit, offset=offset
        )
        return NotificationsOut(
            unread=unread,
            items=[
                NotificationOut(
                    id=row.id,
                    kind=row.kind,
                    title=row.title,
                    document_id=row.document_id,
                    created_at=row.created_at,
                    read=row.read_at is not None,
                )
                for row in rows
            ],
        )


@router.post("/notifications/{notification_id}/read", status_code=204)
def read_notification(notification_id: uuid.UUID, ctx: Ctx, collab: Collab, who: User_) -> Response:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        collab.mark_read(session, who, notification_id)
    return Response(status_code=204)


@router.post("/notifications/read-all")
def read_all(ctx: Ctx, collab: Collab, who: User_) -> dict[str, int]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return {"marked": collab.mark_all_read(session, who)}
