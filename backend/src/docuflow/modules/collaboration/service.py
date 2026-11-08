"""Comments, tasks and in-app notifications on documents (F10).

Rules that hold throughout:
- Everything hangs off a document, and only people who may read the document see any of it.
  A task cannot be given to, and a mention does not reach, someone who cannot read the document,
  so nothing leaks through a notification.
- Commenting needs the ``annotate`` right. Creating or reassigning a task needs ``edit_metadata``.
  The person a task is for, and the person who made it, may mark it done or cancelled.
- A person may remove their own comment. Anyone with ``delete`` on the document may remove any.
- Notifications are for one person and only say what happened and where. They are never created
  for the person who caused the event.
- Every change is audited.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from docuflow.modules.collaboration.models import Comment, Mention, Notification, Task
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.models import User
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.models import Document
from docuflow.modules.repository.service import RepositoryService
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import utcnow
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound

MAX_COMMENT = 4000
MAX_TITLE = 200
MAX_DESCRIPTION = 4000
MAX_MENTIONS = 20
MAX_ASSIGNABLE = 200
PRIORITIES = ("low", "normal", "high")
STATUSES = ("open", "done", "cancelled")
MENTION = re.compile(r"(?<![\w@.])@([A-Za-z0-9][A-Za-z0-9._-]{0,63})")


@dataclass(frozen=True)
class CommentView:
    comment: Comment
    author_name: str
    mentioned: list[str]
    can_delete: bool


@dataclass(frozen=True)
class TaskView:
    task: Task
    document_title: str
    assignee_name: str
    can_change: bool


@dataclass(frozen=True)
class Person:
    id: uuid.UUID
    name: str


def mention_tokens(body: str) -> list[str]:
    """The names after an @ sign, without repeats, in order."""
    return list(dict.fromkeys(match.lower() for match in MENTION.findall(body)))[:MAX_MENTIONS]


class CollaborationService:
    def __init__(self, audit: AuditLog, policy: PolicyService, repo: RepositoryService) -> None:
        self._audit = audit
        self._policy = policy
        self._repo = repo

    # Helpers

    def _record(
        self,
        session: Session,
        principal: Principal,
        action: str,
        object_type: str,
        object_id: uuid.UUID,
        details: dict[str, object],
    ) -> None:
        self._audit.record(
            session,
            principal.tenant_id,
            action,
            actor=AuditActor(
                principal.user_id, "user" if principal.kind == "session" else "api_key"
            ),
            object_type=object_type,
            object_id=str(object_id),
            details=details,
        )

    def _can(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        document: Document,
        right: str,
    ) -> bool:
        return (
            self._policy.tree(session, tenant_id, user_id)
            .decide(document.acl_anchor_id, right)
            .allowed
        )

    def _require(
        self, session: Session, principal: Principal, document: Document, right: str
    ) -> None:
        if not self._can(session, principal.tenant_id, principal.user_id, document, right):
            raise Forbidden("You do not have permission to do that.", details={"required": right})

    @staticmethod
    def _notify(
        session: Session,
        principal: Principal,
        user_id: uuid.UUID,
        kind: str,
        document: Document,
        ref_id: uuid.UUID,
        title: str,
    ) -> None:
        if user_id == principal.user_id:
            return
        session.add(
            Notification(
                tenant_id=principal.tenant_id,
                user_id=user_id,
                kind=kind,
                document_id=document.id,
                ref_id=ref_id,
                title=title[:300],
                actor_id=principal.user_id,
            )
        )

    @staticmethod
    def _names(session: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        return {
            user.id: user.display_name
            for user in session.scalars(select(User).where(User.id.in_(ids)))
        }

    # People who can be given work on a document

    def assignable(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> list[Person]:
        document = self._repo.get_document(session, principal, document_id)
        people: list[Person] = []
        for user in session.scalars(
            select(User)
            .where(User.status == "active", User.kind == "person")
            .order_by(User.display_name, User.id)
            .limit(MAX_ASSIGNABLE)
        ):
            if self._can(session, principal.tenant_id, user.id, document, "read"):
                people.append(Person(user.id, user.display_name))
        return people

    # Comments

    def comments(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> list[CommentView]:
        document = self._repo.get_document(session, principal, document_id)
        can_delete_any = self._can(
            session, principal.tenant_id, principal.user_id, document, "delete"
        )
        rows = list(
            session.scalars(
                select(Comment)
                .where(Comment.document_id == document.id)
                .order_by(Comment.created_at, Comment.id)
            )
        )
        mentions: dict[uuid.UUID, list[uuid.UUID]] = {}
        for mention in session.scalars(
            select(Mention).where(Mention.comment_id.in_([row.id for row in rows]))
        ):
            mentions.setdefault(mention.comment_id, []).append(mention.user_id)
        names = self._names(
            session,
            {row.author_id for row in rows} | {u for ids in mentions.values() for u in ids},
        )
        return [
            CommentView(
                row,
                names.get(row.author_id, "Unknown"),
                [names.get(u, "Unknown") for u in mentions.get(row.id, [])],
                can_delete_any or row.author_id == principal.user_id,
            )
            for row in rows
        ]

    def add_comment(
        self, session: Session, principal: Principal, document_id: uuid.UUID, body: str
    ) -> CommentView:
        document = self._repo.get_document(session, principal, document_id)
        self._require(session, principal, document, "annotate")
        body = body.strip()
        if not body or len(body) > MAX_COMMENT:
            raise DomainError(f"A comment must be 1 to {MAX_COMMENT} characters.")
        comment = Comment(
            tenant_id=principal.tenant_id,
            document_id=document.id,
            author_id=principal.user_id,
            body=body,
        )
        session.add(comment)
        session.flush()
        mentioned: list[str] = []
        tokens = mention_tokens(body)
        if tokens:
            for user in session.scalars(
                select(User).where(
                    User.status == "active",
                    func.lower(func.split_part(User.email, "@", 1)).in_(tokens),
                )
            ):
                # Someone who cannot read the document is not mentioned, so nothing leaks to them.
                if not self._can(session, principal.tenant_id, user.id, document, "read"):
                    continue
                session.add(
                    Mention(tenant_id=principal.tenant_id, comment_id=comment.id, user_id=user.id)
                )
                mentioned.append(user.display_name)
                self._notify(
                    session,
                    principal,
                    user.id,
                    "mention",
                    document,
                    comment.id,
                    f"You were mentioned on {document.title}",
                )
        self._record(
            session,
            principal,
            "document.commented",
            "document",
            document.id,
            {"comment": str(comment.id), "mentions": mentioned},
        )
        author = self._names(session, {principal.user_id})
        return CommentView(comment, author.get(principal.user_id, "Unknown"), mentioned, True)

    def delete_comment(
        self, session: Session, principal: Principal, document_id: uuid.UUID, comment_id: uuid.UUID
    ) -> None:
        document = self._repo.get_document(session, principal, document_id)
        comment = session.get(Comment, comment_id)
        if comment is None or comment.document_id != document.id:
            raise NotFound("Comment not found.")
        if comment.author_id != principal.user_id:
            self._require(session, principal, document, "delete")
        session.delete(comment)
        self._record(
            session,
            principal,
            "document.comment_removed",
            "document",
            document.id,
            {"comment": str(comment_id), "author": str(comment.author_id)},
        )

    # Tasks

    def _view(self, session: Session, principal: Principal, tasks: list[Task]) -> list[TaskView]:
        documents = {
            doc.id: doc
            for doc in session.scalars(
                select(Document).where(Document.id.in_({t.document_id for t in tasks}))
            )
        }
        names = self._names(session, {t.assignee_id for t in tasks})
        views: list[TaskView] = []
        for task in tasks:
            document = documents[task.document_id]
            can_change = (
                task.assignee_id == principal.user_id
                or task.created_by == principal.user_id
                or self._can(
                    session, principal.tenant_id, principal.user_id, document, "edit_metadata"
                )
            )
            views.append(
                TaskView(task, document.title, names.get(task.assignee_id, "Unknown"), can_change)
            )
        return views

    def tasks(
        self,
        session: Session,
        principal: Principal,
        *,
        document_id: uuid.UUID | None = None,
        mine: bool = False,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskView]:
        if status is not None and status not in STATUSES:
            raise DomainError("The status must be open, done or cancelled.")
        query = select(Task).order_by(Task.due_at.asc().nulls_last(), Task.created_at, Task.id)
        if document_id is not None:
            document = self._repo.get_document(session, principal, document_id)
            query = query.where(Task.document_id == document.id)
        if mine:
            query = query.where(Task.assignee_id == principal.user_id)
        elif document_id is None:
            query = query.where(
                (Task.assignee_id == principal.user_id) | (Task.created_by == principal.user_id)
            )
        if status is not None:
            query = query.where(Task.status == status)
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        visible: list[Task] = []
        for task in session.scalars(query.limit(500)):
            owner = session.get(Document, task.document_id)
            if owner is not None and tree.decide(owner.acl_anchor_id, "read").allowed:
                visible.append(task)
        return self._view(session, principal, visible[offset : offset + limit])

    def create_task(
        self,
        session: Session,
        principal: Principal,
        document_id: uuid.UUID,
        *,
        title: str,
        description: str,
        assignee_id: uuid.UUID,
        priority: str,
        due_at: datetime | None,
    ) -> TaskView:
        document = self._repo.document_for_change(session, principal, document_id, "edit_metadata")
        title = title.strip()
        if not title or len(title) > MAX_TITLE:
            raise DomainError(f"A task title must be 1 to {MAX_TITLE} characters.")
        if len(description) > MAX_DESCRIPTION:
            raise DomainError(f"A description can be up to {MAX_DESCRIPTION} characters.")
        if priority not in PRIORITIES:
            raise DomainError("The priority must be low, normal or high.")
        self._check_assignee(session, principal, document, assignee_id)
        task = Task(
            tenant_id=principal.tenant_id,
            document_id=document.id,
            title=title,
            description=description.strip(),
            priority=priority,
            assignee_id=assignee_id,
            created_by=principal.user_id,
            due_at=due_at,
        )
        session.add(task)
        session.flush()
        self._notify(
            session,
            principal,
            assignee_id,
            "task_assigned",
            document,
            task.id,
            f"Task for you: {title}",
        )
        self._record(
            session,
            principal,
            "task.created",
            "task",
            task.id,
            {"document": str(document.id), "assignee": str(assignee_id), "priority": priority},
        )
        return self._view(session, principal, [task])[0]

    def _check_assignee(
        self, session: Session, principal: Principal, document: Document, assignee_id: uuid.UUID
    ) -> None:
        user = session.get(User, assignee_id)
        if user is None or user.status != "active" or user.kind != "person":
            raise DomainError("That person cannot be given a task.")
        if not self._can(session, principal.tenant_id, assignee_id, document, "read"):
            raise DomainError(
                "That person cannot read this document, so they cannot be given a task on it."
            )

    def update_task(
        self,
        session: Session,
        principal: Principal,
        task_id: uuid.UUID,
        row_version: int,
        *,
        status: str | None = None,
        title: str | None = None,
        priority: str | None = None,
        assignee_id: uuid.UUID | None = None,
        due_at: datetime | None = None,
        clear_due: bool = False,
    ) -> TaskView:
        task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        document = session.get(Document, task.document_id) if task else None
        if (
            task is None
            or document is None
            or not self._can(session, principal.tenant_id, principal.user_id, document, "read")
        ):
            raise NotFound("Task not found.")
        if task.row_version != row_version:
            raise Conflict("This task was changed by someone else. Reload and try again.")
        details_changed = (
            any(item is not None for item in (title, priority, assignee_id, due_at)) or clear_due
        )
        if details_changed:
            document = self._repo.document_for_change(
                session, principal, document.id, "edit_metadata"
            )
        changes: dict[str, object] = {}
        if title is not None and title.strip() != task.title:
            clean = title.strip()
            if not clean or len(clean) > MAX_TITLE:
                raise DomainError(f"A task title must be 1 to {MAX_TITLE} characters.")
            changes["title"] = [task.title, clean]
            task.title = clean
        if priority is not None and priority != task.priority:
            if priority not in PRIORITIES:
                raise DomainError("The priority must be low, normal or high.")
            changes["priority"] = [task.priority, priority]
            task.priority = priority
        if due_at is not None or clear_due:
            new_due = None if clear_due else due_at
            if new_due != task.due_at:
                changes["due_at"] = [str(task.due_at), str(new_due)]
                task.due_at = new_due
        if assignee_id is not None and assignee_id != task.assignee_id:
            self._check_assignee(session, principal, document, assignee_id)
            changes["assignee"] = [str(task.assignee_id), str(assignee_id)]
            task.assignee_id = assignee_id
            self._notify(
                session,
                principal,
                assignee_id,
                "task_assigned",
                document,
                task.id,
                f"Task for you: {task.title}",
            )
        if status is not None and status != task.status:
            if status not in STATUSES:
                raise DomainError("The status must be open, done or cancelled.")
            allowed = (
                task.assignee_id == principal.user_id
                or task.created_by == principal.user_id
                or self._can(
                    session, principal.tenant_id, principal.user_id, document, "edit_metadata"
                )
            )
            if not allowed:
                raise Forbidden("Only the person it is for, or who made it, can do that.")
            changes["status"] = [task.status, status]
            task.status = status
            task.completed_at = utcnow() if status != "open" else None
            task.completed_by = principal.user_id if status != "open" else None
            if status == "done" and task.created_by:
                self._notify(
                    session,
                    principal,
                    task.created_by,
                    "task_done",
                    document,
                    task.id,
                    f"Task done: {task.title}",
                )
        if changes:
            task.row_version += 1
            self._record(session, principal, "task.updated", "task", task.id, {"changes": changes})
        return self._view(session, principal, [task])[0]

    # Notifications

    def notifications(
        self, session: Session, principal: Principal, *, unread_only: bool, limit: int, offset: int
    ) -> tuple[list[Notification], int]:
        query = select(Notification).where(Notification.user_id == principal.user_id)
        unread = int(
            session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == principal.user_id, Notification.read_at.is_(None))
            )
            or 0
        )
        if unread_only:
            query = query.where(Notification.read_at.is_(None))
        rows = list(
            session.scalars(
                query.order_by(Notification.created_at.desc(), Notification.id)
                .limit(limit)
                .offset(offset)
            )
        )
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        shown: list[Notification] = []
        for row in rows:
            document = session.get(Document, row.document_id) if row.document_id else None
            if document is None or tree.decide(document.acl_anchor_id, "read").allowed:
                shown.append(row)
        return shown, unread

    def mark_read(self, session: Session, principal: Principal, notification_id: uuid.UUID) -> None:
        row = session.get(Notification, notification_id)
        if row is None or row.user_id != principal.user_id:
            raise NotFound("Notification not found.")
        if row.read_at is None:
            row.read_at = utcnow()

    def mark_all_read(self, session: Session, principal: Principal) -> int:
        result = session.execute(
            update(Notification)
            .where(Notification.user_id == principal.user_id, Notification.read_at.is_(None))
            .values(read_at=utcnow())
        )
        return int(getattr(result, "rowcount", 0) or 0)
