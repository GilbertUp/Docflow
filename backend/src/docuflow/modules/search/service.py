"""Finding documents by their words and details (F08).

Rules that hold throughout:
- Only what a person may read is returned, and counts never reveal anything hidden. Permissions
  are checked on every candidate, never trusted from the index.
- Text is content. A document whose text alone matches is shown only to someone with the
  ``preview`` right on it, and a snippet is only ever cut from text that person may see.
- The index is a copy rebuilt by a background job when a document's title, details or text change,
  so a search can be a little behind, and never exposes what was removed from view.
- Queries are plain words. Quotes group a phrase, ``or`` joins alternatives and ``-`` excludes.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from docuflow.modules.documents.models import DocumentVersion, OcrResult, Page
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.indexing.models import DocumentType, IndexField, IndexValue
from docuflow.modules.repository.models import Document
from docuflow.modules.search.models import SearchEntry
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import Database, utcnow
from docuflow.platform.errors import DomainError
from docuflow.platform.jobs import JobContext, enqueue

JOB_TYPE = "search.index_document"
MAX_QUERY = 200
MAX_CANDIDATES = 500
MAX_BODY = 2_000_000
HEADLINE_FROM = 20_000
OPEN, CLOSE = "[[", "]]"


def request_indexing(session: Session, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """Queues rebuilding a document's search row, in the transaction that changed it."""
    enqueue(
        session,
        tenant_id,
        JOB_TYPE,
        f"search:{document_id}:{uuid.uuid4().hex}",
        queue="index",
        payload={"document_id": str(document_id)},
    )


@dataclass(frozen=True)
class Hit:
    document: Document
    type_name: str | None
    matched_in: str
    rank: float
    snippet: str | None


@dataclass(frozen=True)
class Results:
    hits: list[Hit]
    total: int
    truncated: bool


def clean_query(raw: str) -> str:
    query = " ".join(raw.split())
    if not query:
        raise DomainError("Type something to search for.")
    if len(query) > MAX_QUERY:
        raise DomainError(f"Searches can be up to {MAX_QUERY} characters.")
    return query


class SearchService:
    def __init__(self, audit: AuditLog, policy: PolicyService) -> None:
        self._audit = audit
        self._policy = policy

    # Building the index (called by the worker)

    @staticmethod
    def _values_text(session: Session, document_id: uuid.UUID) -> list[str]:
        words: list[str] = []
        rows = session.execute(
            select(IndexValue, IndexField.label)
            .join(IndexField, IndexField.id == IndexValue.field_id)
            .where(IndexValue.document_id == document_id)
            .order_by(IndexField.position)
        )
        for value, label in rows:
            for item in (value.value_text, value.value_number, value.value_date, value.value_bool):
                if item is not None:
                    shown = ("yes" if item else "no") if isinstance(item, bool) else str(item)
                    words.append(f"{label} {shown}")
        return words

    @staticmethod
    def _page_text(session: Session, version_id: uuid.UUID) -> str:
        pages = list(
            session.scalars(select(Page).where(Page.version_id == version_id).order_by(Page.number))
        )
        if not pages:
            return ""
        latest: dict[uuid.UUID, str] = {}
        for row in session.scalars(
            select(OcrResult)
            .where(OcrResult.page_id.in_([page.id for page in pages]))
            .order_by(OcrResult.created_at, OcrResult.id)
        ):
            latest[row.page_id] = row.text  # the newest row comes last
        joined = "\n".join(latest[page.id] for page in pages if page.id in latest)
        return joined[:MAX_BODY]

    def run(self, db: Database, ctx: JobContext) -> None:
        document_id = uuid.UUID(ctx.payload["document_id"])
        with db.tenant_session(ctx.tenant_id) as session:
            document = session.get(Document, document_id)
            if document is None or document.status != "active":
                session.execute(delete(SearchEntry).where(SearchEntry.document_id == document_id))
                return
            kind = (
                session.get(DocumentType, document.document_type_id)
                if document.document_type_id
                else None
            )
            plain = re.sub(r"[\W_]+", " ", document.title).strip()
            meta = [document.title, plain, *([kind.name] if kind else [])]
            meta += self._values_text(session, document.id)
            version = (
                session.get(DocumentVersion, document.current_version_id)
                if document.current_version_id
                else None
            )
            body = self._page_text(session, version.id) if version else ""
            values: dict[str, Any] = {
                "tenant_id": ctx.tenant_id,
                "document_id": document.id,
                "version_number": version.number if version else 0,
                "meta": "\n".join(meta),
                "body": body,
                "indexed_at": utcnow(),
            }
            statement = insert(SearchEntry).values(id=uuid.uuid4(), **values)
            session.execute(
                statement.on_conflict_do_update(constraint="uq_search_entry_document", set_=values)
            )

    # Searching

    def search(
        self,
        session: Session,
        principal: Principal,
        query: str,
        *,
        limit: int,
        offset: int,
        type_id: uuid.UUID | None = None,
        cabinet_id: uuid.UUID | None = None,
    ) -> Results:
        words = clean_query(query)
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        tsquery = func.websearch_to_tsquery("simple", words)
        meta_hit = SearchEntry.meta_tsv.op("@@")(tsquery)
        body_hit = SearchEntry.body_tsv.op("@@")(tsquery)
        rank = func.ts_rank_cd(SearchEntry.meta_tsv, tsquery) * 2 + func.ts_rank_cd(
            SearchEntry.body_tsv, tsquery
        )
        statement = (
            select(Document, meta_hit, body_hit, rank)
            .join(SearchEntry, SearchEntry.document_id == Document.id)
            .where((meta_hit | body_hit), Document.status == "active")
            .order_by(rank.desc(), Document.id)
            .limit(MAX_CANDIDATES + 1)
        )
        if type_id is not None:
            statement = statement.where(Document.document_type_id == type_id)
        if cabinet_id is not None:
            statement = statement.where(Document.cabinet_id == cabinet_id)
        candidates = session.execute(statement).all()
        truncated = len(candidates) > MAX_CANDIDATES
        visible: list[tuple[Document, str, float, bool]] = []
        for document, in_meta, in_body, score in candidates[:MAX_CANDIDATES]:
            if not tree.decide(document.acl_anchor_id, "read").allowed:
                continue
            can_see_text = tree.decide(document.acl_anchor_id, "preview").allowed
            if not in_meta and not can_see_text:
                continue  # only the text matched, and this person may not see the text
            shown_in = (
                "both"
                if in_meta and in_body and can_see_text
                else "text"
                if in_body and can_see_text and not in_meta
                else "details"
            )
            visible.append((document, shown_in, float(score), can_see_text))
        page = visible[offset : offset + limit]
        snippets = self._snippets(session, [d.id for d, _, _, see in page if see], tsquery)
        names = {
            row.id: row.name
            for row in session.scalars(
                select(DocumentType).where(
                    DocumentType.id.in_(
                        {d.document_type_id for d, *_ in page if d.document_type_id}
                    )
                )
            )
        }
        hits = [
            Hit(
                document,
                names.get(document.document_type_id) if document.document_type_id else None,
                shown_in,
                score,
                snippets.get(document.id),
            )
            for document, shown_in, score, _see in page
        ]
        self._audit.record(
            session,
            principal.tenant_id,
            "search.performed",
            actor=AuditActor(
                principal.user_id, "user" if principal.kind == "session" else "api_key"
            ),
            object_type="search",
            object_id=None,
            details={
                "query": words,
                "results": len(visible),
                "type": str(type_id) if type_id else None,
            },
        )
        return Results(hits, len(visible), truncated)

    @staticmethod
    def _snippets(session: Session, ids: list[uuid.UUID], tsquery: Any) -> dict[uuid.UUID, str]:
        """A few words around each match, with the matches marked ``[[like this]]``."""
        if not ids:
            return {}
        rows = session.execute(
            select(
                SearchEntry.document_id,
                func.ts_headline(
                    "simple",
                    func.left(SearchEntry.body, HEADLINE_FROM),
                    tsquery,
                    f"StartSel={OPEN}, StopSel={CLOSE}, MaxFragments=2, MaxWords=24, "
                    "MinWords=8, FragmentDelimiter= … ",
                ),
            ).where(SearchEntry.document_id.in_(ids))
        )
        return {document_id: str(snippet) for document_id, snippet in rows if OPEN in str(snippet)}


__all__ = ["JOB_TYPE", "Hit", "Results", "SearchService", "clean_query", "request_indexing"]
