"""Answering questions from an organization's own documents, with the sources shown (F18).

Rules that hold throughout (ADR 0013):
- What may be shown is decided before any model is involved. Evidence is found through the
  search index, and a passage counts only if the person asking may read the document and see its
  text. A model never sees, and an answer never quotes, anything the person could not open.
- Without enough evidence there is no answer. The model is not called, and the person is told.
- With no model set up, or generated answers off for the organization, the assistant returns the
  best passages as they are, labelled as extracts. It never presents them as written answers.
- Generated answers start switched off for every organization. An organization can exclude
  cabinets, whose text is then never sent to an AI provider.
- A generated answer is checked after the fact. Every citation must name a passage that was
  supplied. A sentence with a missing or invalid citation is removed, and the answer is flagged.
  If nothing checkable is left, the person gets the extracts instead.
- The model is given no tools. Passages are fenced as data and told to be ignored as instructions.
- The prompt is never stored. The record keeps the question, the answer, and where each
  citation came from.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from docuflow.modules.assistant import retrieval
from docuflow.modules.assistant.models import AssistantQuery, AssistantSetting
from docuflow.modules.assistant.providers import LlmProvider, ProviderError
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.models import Cabinet, Document
from docuflow.modules.repository.service import RepositoryService
from docuflow.modules.search.models import SearchChunk
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound

log = structlog.get_logger("docuflow.assistant")

MANAGE = "integrations.manage"
MAX_QUESTION = 500
CANDIDATES = 80
MAX_PASSAGES = 6
MIN_COVERAGE = 0.5
SNIPPET = 320


@dataclass(frozen=True)
class Source:
    number: int
    document: Document
    page: int
    chunk_id: uuid.UUID
    text: str
    coverage: float

    @property
    def snippet(self) -> str:
        return (
            self.text if len(self.text) <= SNIPPET else self.text[:SNIPPET].rsplit(" ", 1)[0] + "…"
        )


@dataclass(frozen=True)
class Answer:
    # generated, extracts or insufficient_evidence
    mode: str
    answer: str | None
    sources: list[Source]
    notices: list[str] = field(default_factory=list)
    flagged: bool = False
    query_id: uuid.UUID | None = None


@dataclass(frozen=True)
class SettingsView:
    generation_enabled: bool
    excluded_cabinet_ids: list[str]
    provider_configured: bool
    provider: str | None
    model: str | None


class AssistantService:
    def __init__(
        self,
        audit: AuditLog,
        policy: PolicyService,
        repo: RepositoryService,
        provider: LlmProvider | None = None,
    ) -> None:
        self._audit = audit
        self._policy = policy
        self._repo = repo
        self._provider = provider

    def _actor(self, principal: Principal) -> AuditActor:
        return AuditActor(principal.user_id, "user" if principal.kind == "session" else "api_key")

    # Settings

    @staticmethod
    def _row(session: Session) -> AssistantSetting | None:
        return session.scalar(select(AssistantSetting))

    def settings(self, session: Session) -> SettingsView:
        row = self._row(session)
        return SettingsView(
            row.generation_enabled if row else False,
            list(row.excluded_cabinet_ids or []) if row else [],
            self._provider is not None,
            self._provider.name if self._provider else None,
            self._provider.model if self._provider else None,
        )

    def set_settings(
        self,
        session: Session,
        principal: Principal,
        generation_enabled: bool,
        excluded_cabinet_ids: list[uuid.UUID],
    ) -> SettingsView:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        if MANAGE not in held:
            raise Forbidden("You do not have permission to do that.", details={"required": MANAGE})
        if generation_enabled and self._provider is None:
            raise Conflict(
                "No AI service is set up on this server, so generated answers cannot be turned on."
            )
        for cabinet_id in excluded_cabinet_ids:
            if session.get(Cabinet, cabinet_id) is None:
                raise NotFound("One of those cabinets does not exist.")
        before = self.settings(session)
        row = self._row(session)
        if row is None:
            row = AssistantSetting(tenant_id=principal.tenant_id)
            session.add(row)
        row.generation_enabled = generation_enabled
        row.excluded_cabinet_ids = [str(c) for c in dict.fromkeys(excluded_cabinet_ids)]
        row.updated_by = principal.user_id
        session.flush()
        self._audit.record(
            session,
            principal.tenant_id,
            "assistant.settings_changed",
            actor=self._actor(principal),
            object_type="assistant",
            details={
                "generation_enabled": [before.generation_enabled, generation_enabled],
                "excluded_cabinets": [
                    len(before.excluded_cabinet_ids),
                    len(row.excluded_cabinet_ids),
                ],
            },
        )
        return self.settings(session)

    # Asking

    def _evidence(
        self,
        session: Session,
        principal: Principal,
        terms: list[str],
        cabinet_id: uuid.UUID | None,
    ) -> list[Source]:
        """Passages that match the question and that this person may read, best first."""
        query = func.to_tsquery("simple", " | ".join(terms))
        rank = func.ts_rank_cd(SearchChunk.tsv, query)
        statement = (
            select(SearchChunk, Document)
            .join(Document, Document.id == SearchChunk.document_id)
            .where(SearchChunk.tsv.op("@@")(query), Document.status == "active")
            .order_by(rank.desc(), SearchChunk.id)
            .limit(CANDIDATES)
        )
        if cabinet_id is not None:
            statement = statement.where(Document.cabinet_id == cabinet_id)
        tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
        found: list[tuple[float, int, SearchChunk, Document]] = []
        for position, (chunk, document) in enumerate(session.execute(statement).all()):
            if not (
                tree.decide(document.acl_anchor_id, "read").allowed
                and tree.decide(document.acl_anchor_id, "preview").allowed
            ):
                continue
            share = retrieval.coverage(terms, chunk.text)
            if share >= MIN_COVERAGE:
                found.append((share, -position, chunk, document))
        # More of the question's words first, then the search engine's own ranking.
        found.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [
            Source(number, document, chunk.page_number, chunk.id, chunk.text, share)
            for number, (share, _p, chunk, document) in enumerate(found[:MAX_PASSAGES], start=1)
        ]

    def ask(
        self,
        session: Session,
        principal: Principal,
        question: str,
        cabinet_id: uuid.UUID | None = None,
    ) -> Answer:
        question = " ".join(question.split())
        if not question or len(question) > MAX_QUESTION:
            raise DomainError(f"Ask a question of 1 to {MAX_QUESTION} characters.")
        terms = retrieval.significant_terms(question)
        if not terms:
            raise DomainError(
                "Add a few specific words to your question, such as a name or a date."
            )
        sources = self._evidence(session, principal, terms, cabinet_id)
        if not sources:
            answer = Answer(
                "insufficient_evidence",
                None,
                [],
                ["The documents you can read do not contain enough evidence to answer this."],
            )
        else:
            answer = self._respond(session, question, sources)
        return self._record(session, principal, question, answer, sources)

    def _respond(self, session: Session, question: str, sources: list[Source]) -> Answer:
        settings = self.settings(session)
        notices: list[str] = []
        extracts = Answer("extracts", None, sources, notices)
        if self._provider is None:
            notices.append("No AI service is set up, so these are extracts from your documents.")
            return extracts
        if not settings.generation_enabled:
            notices.append(
                "Generated answers are switched off for your organization, so these are extracts."
            )
            return extracts
        excluded = set(settings.excluded_cabinet_ids)
        sendable = [s for s in sources if str(s.document.cabinet_id) not in excluded]
        left_out = len(sources) - len(sendable)
        if left_out:
            notices.append(
                f"{left_out} passage{'s' if left_out != 1 else ''} from excluded cabinets "
                "were not sent to the AI service."
            )
        if not sendable:
            notices.append("All the evidence is in excluded cabinets, so these are extracts.")
            return extracts
        try:
            reply = self._provider.generate(
                retrieval.SYSTEM_PROMPT,
                retrieval.build_user_prompt(
                    question, [(s.number, s.document.title, s.page, s.text) for s in sendable]
                ),
            )
        except ProviderError as exc:
            log.warning("assistant_provider_failed", reason=str(exc))
            notices.append("The AI service could not be reached, so these are extracts.")
            return extracts
        checked = retrieval.check_answer(reply, [s.number for s in sendable])
        if checked.no_evidence:
            return Answer(
                "insufficient_evidence",
                None,
                [],
                ["The documents you can read do not contain enough evidence to answer this."],
            )
        if not checked.text:
            notices.append(
                "The generated answer could not be checked against the documents, "
                "so these are extracts."
            )
            return Answer("extracts", None, sources, notices, flagged=True)
        if checked.flagged:
            notices.append(
                "Parts of the generated answer had no valid source and were removed. "
                "Check the sources below."
            )
        cited = [s for s in sendable if s.number in set(checked.cited)]
        return Answer("generated", checked.text, cited, notices, flagged=checked.flagged)

    def _record(
        self,
        session: Session,
        principal: Principal,
        question: str,
        answer: Answer,
        considered: list[Source],
    ) -> Answer:
        generated = answer.mode == "generated" or (answer.flagged and answer.mode == "extracts")
        row = AssistantQuery(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            question=question,
            mode=answer.mode,
            answer=answer.answer,
            citations=[
                {
                    "n": s.number,
                    "document_id": str(s.document.id),
                    "page": s.page,
                    "chunk_id": str(s.chunk_id),
                }
                for s in answer.sources
            ],
            flagged=answer.flagged,
            provider=self._provider.name if self._provider and generated else None,
            model=self._provider.model if self._provider and generated else None,
            best_coverage=max((s.coverage for s in considered), default=0.0),
        )
        session.add(row)
        session.flush()
        self._audit.record(
            session,
            principal.tenant_id,
            "assistant.asked",
            actor=self._actor(principal),
            object_type="assistant",
            object_id=str(row.id),
            details={
                "mode": answer.mode,
                "sources": len(answer.sources),
                "flagged": answer.flagged,
                "question": question[:200],
            },
        )
        return Answer(
            answer.mode, answer.answer, answer.sources, answer.notices, answer.flagged, row.id
        )

    def history(
        self, session: Session, principal: Principal, limit: int = 20
    ) -> list[AssistantQuery]:
        return list(
            session.scalars(
                select(AssistantQuery)
                .where(AssistantQuery.user_id == principal.user_id)
                .order_by(AssistantQuery.created_at.desc(), AssistantQuery.id)
                .limit(min(limit, 50))
            )
        )
