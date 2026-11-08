"""Reading, exporting and checking the audit trail (F16).

Rules that hold throughout:
- Reading the trail needs the ``audit.read`` system right. The trail records what people did, so it
  is not shown to everyone who can see the documents involved.
- Only a person's own session or API key can read it, and reading is bounded: a page is at most
  200 events and an export at most 50,000, with a flag when more exist.
- Exporting and verifying are themselves audited, with the filters used, so the people who look at
  the trail are on the trail.
- An export carries each event's hash and the hash before it, plus the head of the chain when it
  was made, so a copy held elsewhere can be checked against the live trail.
- Spreadsheet formulas are neutralised in exports: a cell that starts with ``=``, ``+``, ``-``,
  ``@`` or a tab is prefixed, so an event that holds attacker-chosen text cannot run in a
  spreadsheet.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.models import User
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.service import RepositoryService
from docuflow.platform.audit import AuditActor, AuditLog, CheckpointReader, VerificationResult
from docuflow.platform.errors import DomainError, Forbidden
from docuflow.platform.models import AuditEvent

READ = "audit.read"
MAX_PAGE = 200
MAX_EXPORT = 50_000
EXPORT_COLUMNS = [
    "seq",
    "occurred_at",
    "actor_id",
    "actor_name",
    "actor_type",
    "action",
    "object_type",
    "object_id",
    "result",
    "ip",
    "correlation_id",
    "details",
    "prev_hash",
    "hash",
]


@dataclass(frozen=True)
class Filters:
    actor_id: uuid.UUID | None = None
    action: str | None = None
    action_prefix: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    result: str | None = None
    since: datetime | None = None
    until: datetime | None = None

    def described(self) -> dict[str, str]:
        """The filters in use, as text, for the record of who looked."""
        return {k: str(v) for k, v in vars(self).items() if v is not None}


@dataclass(frozen=True)
class EventView:
    event: AuditEvent
    actor_name: str | None


@dataclass(frozen=True)
class Page:
    events: list[EventView]
    next_before_seq: int | None


@dataclass(frozen=True)
class Export:
    rows: Iterator[bytes]
    count: int
    truncated: bool
    head_seq: int
    head_hash: str


def label_of(action: str) -> str:
    """A readable name for an action such as ``document.version_added``."""
    last = action.split(".")[-1].replace("_", " ")
    return last[:1].upper() + last[1:]


def safe_cell(value: Any) -> str:
    """A value for a CSV cell that a spreadsheet will not run as a formula."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


class AuditQueryService:
    def __init__(self, audit: AuditLog, policy: PolicyService, repo: RepositoryService) -> None:
        self._audit = audit
        self._policy = policy
        self._repo = repo

    def _require(self, session: Session, principal: Principal) -> None:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        if READ not in held:
            raise Forbidden("You do not have permission to do that.", details={"required": READ})

    def _record(
        self, session: Session, principal: Principal, action: str, details: dict[str, Any]
    ) -> None:
        self._audit.record(
            session,
            principal.tenant_id,
            action,
            actor=AuditActor(
                principal.user_id, "user" if principal.kind == "session" else "api_key"
            ),
            object_type="audit",
            details=details,
        )

    @staticmethod
    def _query(filters: Filters) -> Any:
        query = select(AuditEvent)
        if filters.actor_id is not None:
            query = query.where(AuditEvent.actor_id == filters.actor_id)
        if filters.action:
            query = query.where(AuditEvent.action == filters.action)
        if filters.action_prefix:
            escaped = (
                filters.action_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            query = query.where(AuditEvent.action.like(escaped + "%", escape="\\"))
        if filters.object_type:
            query = query.where(AuditEvent.object_type == filters.object_type)
        if filters.object_id:
            query = query.where(AuditEvent.object_id == filters.object_id)
        if filters.result:
            query = query.where(AuditEvent.result == filters.result)
        if filters.since is not None:
            query = query.where(AuditEvent.occurred_at >= filters.since)
        if filters.until is not None:
            query = query.where(AuditEvent.occurred_at < filters.until)
        return query

    @staticmethod
    def _names(session: Session, events: list[AuditEvent]) -> dict[uuid.UUID, str]:
        ids = {e.actor_id for e in events if e.actor_id is not None}
        if not ids:
            return {}
        return {u.id: u.display_name for u in session.scalars(select(User).where(User.id.in_(ids)))}

    def search(
        self,
        session: Session,
        principal: Principal,
        filters: Filters,
        *,
        limit: int = 50,
        before_seq: int | None = None,
    ) -> Page:
        self._require(session, principal)
        limit = max(1, min(limit, MAX_PAGE))
        if filters.since and filters.until and filters.since >= filters.until:
            raise DomainError("The end of the period must be after its start.")
        query = self._query(filters)
        if before_seq is not None:
            query = query.where(AuditEvent.seq < before_seq)
        rows = list(session.scalars(query.order_by(AuditEvent.seq.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        names = self._names(session, rows)
        return Page(
            [EventView(r, names.get(r.actor_id) if r.actor_id else None) for r in rows],
            rows[-1].seq if more and rows else None,
        )

    def timeline(
        self, session: Session, principal: Principal, document_id: uuid.UUID, limit: int = 100
    ) -> list[EventView]:
        """Everything recorded about one document, newest first."""
        self._require(session, principal)
        document = self._repo.get_document(session, principal, document_id)
        rows = list(
            session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.object_type == "document", AuditEvent.object_id == str(document.id)
                )
                .order_by(AuditEvent.seq.desc())
                .limit(max(1, min(limit, MAX_PAGE)))
            )
        )
        names = self._names(session, rows)
        return [EventView(r, names.get(r.actor_id) if r.actor_id else None) for r in rows]

    def export(self, session: Session, principal: Principal, filters: Filters) -> Export:
        """The matching events, oldest first, as CSV."""
        self._require(session, principal)
        if filters.since and filters.until and filters.since >= filters.until:
            raise DomainError("The end of the period must be after its start.")
        head = session.execute(
            select(AuditEvent.seq, AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1)
        ).first()
        rows = list(
            session.scalars(self._query(filters).order_by(AuditEvent.seq).limit(MAX_EXPORT + 1))
        )
        truncated = len(rows) > MAX_EXPORT
        rows = rows[:MAX_EXPORT]
        names = self._names(session, rows)
        self._record(
            session,
            principal,
            "audit.exported",
            {"filters": filters.described(), "events": len(rows), "truncated": truncated},
        )

        def lines() -> Iterator[bytes]:
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\r\n")
            writer.writerow(EXPORT_COLUMNS)
            yield buffer.getvalue().encode("utf-8")
            for event in rows:
                buffer.seek(0)
                buffer.truncate()
                writer.writerow(
                    [
                        safe_cell(v)
                        for v in (
                            event.seq,
                            event.occurred_at.isoformat(),
                            event.actor_id,
                            names.get(event.actor_id) if event.actor_id else "",
                            event.actor_type,
                            event.action,
                            event.object_type,
                            event.object_id,
                            event.result,
                            event.ip,
                            event.correlation_id,
                            json.dumps(event.details, sort_keys=True, default=str),
                            event.prev_hash,
                            event.hash,
                        )
                    ]
                )
                yield buffer.getvalue().encode("utf-8")

        return Export(
            lines(), len(rows), truncated, int(head.seq) if head else 0, head.hash if head else ""
        )

    def verify(
        self, session: Session, principal: Principal, store: CheckpointReader | None
    ) -> tuple[VerificationResult, VerificationResult | None, int]:
        """Walks the whole chain, then compares it with the external checkpoints."""
        self._require(session, principal)
        chain = self._audit.verify(session, principal.tenant_id)
        anchors = (
            self._audit.verify_checkpoints(session, principal.tenant_id, store)
            if store is not None
            else None
        )
        total = int(session.scalar(select(func.count()).select_from(AuditEvent)) or 0)
        self._record(
            session,
            principal,
            "audit.verified",
            {
                "chain_ok": chain.ok,
                "events": chain.checked,
                "first_bad_seq": chain.first_bad_seq,
                "checkpoints_ok": None if anchors is None else anchors.ok,
                "checkpoints": None if anchors is None else anchors.checked,
            },
        )
        return chain, anchors, total
