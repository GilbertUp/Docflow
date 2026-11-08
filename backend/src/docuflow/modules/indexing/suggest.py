"""Proposing a document's type and field values from its text (F07).

Rules that hold throughout:
- Nothing is invented. Every suggestion comes from a rule on the document's own text, and keeps the
  page, characters and word boxes it came from, so a person can check it.
- Confidence decides what happens. At or above the type's auto-accept threshold a valid value is
  filled in at once, but only into an empty field of a document already using the type's current
  fields. A type is only assigned automatically at the same bar. Everything else waits to be
  accepted, and weak guesses are marked as such.
- A person always wins. Saving the document settles each suggestion: matching it accepts it,
  differing from it records a correction (audited, and kept for learning), clearing it rejects it.
- The job runs after a version's text has been read, and can be run again on request.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from docuflow.modules.documents.models import DocumentVersion, OcrResult, Page
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.indexing.finding import (
    Found,
    Located,
    PageText,
    band,
    classification_confidence,
    classify,
    find_values,
    locate,
    score,
    to_value_text,
)
from docuflow.modules.indexing.models import (
    ClassificationResult,
    DocumentType,
    ExtractedField,
    ExtractionFeedback,
    IndexField,
    IndexValue,
)
from docuflow.modules.indexing.service import (
    IndexingService,
    _columns,
    coerce,
    value_text,
)
from docuflow.modules.repository.models import Document
from docuflow.modules.repository.service import RepositoryService
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import Database
from docuflow.platform.errors import Conflict, NotFound
from docuflow.platform.jobs import JobContext, enqueue

JOB_TYPE = "indexing.extract_fields"
NATIVE = "native"
SHORT = 200
TOP_SCORES = 5


def request_field_extraction(session: Session, tenant_id: uuid.UUID, version_id: uuid.UUID) -> None:
    """Queues proposing values for a version, in the transaction that finished reading it."""
    enqueue(
        session,
        tenant_id,
        JOB_TYPE,
        f"fields:{version_id}:{uuid.uuid4().hex}",
        queue="index",
        payload={"version_id": str(version_id)},
    )


@dataclass(frozen=True)
class _Option:
    confidence: float
    hit: Found
    placed: Located
    rendered: str | None
    valid: bool
    value: Any


@dataclass(frozen=True)
class ClassificationInfo:
    type_id: uuid.UUID | None
    type_name: str | None
    confidence: float
    band: str
    applied: bool


@dataclass(frozen=True)
class SuggestedField:
    key: str
    label: str
    data_type: str
    raw_value: str
    value: str | None
    confidence: float
    band: str
    method: str
    page_number: int | None
    bbox: dict[str, Any]
    validation_state: str
    state: str


@dataclass(frozen=True)
class SuggestionView:
    version_number: int
    text_state: str
    classification: ClassificationInfo | None
    fields: list[SuggestedField] = field(default_factory=list)


def _short(value: Any) -> Any:
    return value[:SHORT] if isinstance(value, str) else value


def settle_suggestions(
    session: Session,
    audit: AuditLog,
    principal: Principal,
    document: Document,
    final: dict[str, str | None],
) -> None:
    """Compares what a person saved with what was suggested, and records the outcome.

    ``final`` maps each field key to the saved value as text, or None when it is empty.
    """
    if document.current_version_id is None or document.schema_version_id is None:
        return
    rows = session.execute(
        select(ExtractedField, IndexField)
        .join(IndexField, IndexField.id == ExtractedField.field_id)
        .where(
            ExtractedField.version_id == document.current_version_id,
            IndexField.schema_version_id == document.schema_version_id,
            ExtractedField.state.in_(("suggested", "accepted")),
        )
    )
    actor = AuditActor(principal.user_id, "user" if principal.kind == "session" else "api_key")
    for suggestion, definition in rows:
        saved = final.get(definition.key)
        proposed = suggestion.normalized_value
        if saved is not None and saved == proposed:
            suggestion.state = "accepted"
        elif saved is None and suggestion.state == "suggested":
            suggestion.state = "rejected"
        elif saved != proposed:
            suggestion.state = "corrected"
            session.add(
                ExtractionFeedback(
                    tenant_id=principal.tenant_id,
                    extracted_field_id=suggestion.id,
                    before_value=proposed if proposed is not None else suggestion.raw_value,
                    after_value=saved,
                    corrected_by=principal.user_id,
                )
            )
            audit.record(
                session,
                principal.tenant_id,
                "document.field_corrected",
                actor=actor,
                object_type="document",
                object_id=str(document.id),
                details={
                    "field": definition.key,
                    "before": _short(proposed if proposed is not None else suggestion.raw_value),
                    "after": _short(saved),
                    "confidence": suggestion.confidence,
                },
            )


class SuggestionService:
    def __init__(self, audit: AuditLog, policy: PolicyService, repo: RepositoryService) -> None:
        self._audit = audit
        self._policy = policy
        self._repo = repo

    # Reading

    def get(self, session: Session, principal: Principal, document_id: uuid.UUID) -> SuggestionView:
        document = self._repo.get_document(session, principal, document_id)
        version = (
            session.get(DocumentVersion, document.current_version_id)
            if document.current_version_id
            else None
        )
        if version is None:
            raise NotFound("That document has no file.")
        found = session.scalar(
            select(ClassificationResult).where(ClassificationResult.version_id == version.id)
        )
        info: ClassificationInfo | None = None
        target: DocumentType | None = None
        if document.document_type_id:
            target = session.get(DocumentType, document.document_type_id)
        if found is not None and found.document_type_id is not None:
            proposed = session.get(DocumentType, found.document_type_id)
            if proposed is not None:
                info = ClassificationInfo(
                    proposed.id,
                    proposed.name,
                    found.confidence,
                    band(
                        found.confidence, proposed.auto_accept_threshold, proposed.review_threshold
                    ),
                    applied=document.document_type_id == proposed.id,
                )
                target = target or proposed
        fields: list[SuggestedField] = []
        if target is not None and target.current_schema_version_id is not None:
            rows = session.execute(
                select(ExtractedField, IndexField, Page.number)
                .join(IndexField, IndexField.id == ExtractedField.field_id)
                .outerjoin(Page, Page.id == ExtractedField.page_id)
                .where(
                    ExtractedField.version_id == version.id,
                    IndexField.schema_version_id == target.current_schema_version_id,
                )
                .order_by(IndexField.position)
            )
            for suggestion, definition, number in rows:
                fields.append(
                    SuggestedField(
                        key=definition.key,
                        label=definition.label,
                        data_type=definition.data_type,
                        raw_value=suggestion.raw_value,
                        value=suggestion.normalized_value,
                        confidence=suggestion.confidence,
                        band=band(
                            suggestion.confidence,
                            target.auto_accept_threshold,
                            target.review_threshold,
                        ),
                        method=suggestion.method,
                        page_number=number,
                        bbox=suggestion.bbox,
                        validation_state=suggestion.validation_state,
                        state=suggestion.state,
                    )
                )
        return SuggestionView(version.number, version.text_state, info, fields)

    def request_run(self, db: Database, principal: Principal, document_id: uuid.UUID) -> int:
        """Proposes values again. Returns the number of the version it will read."""
        with db.tenant_session(principal.tenant_id) as session:
            document = self._repo.document_for_change(
                session, principal, document_id, "edit_metadata", lock=True
            )
            version = (
                session.get(DocumentVersion, document.current_version_id)
                if document.current_version_id
                else None
            )
            if version is None:
                raise NotFound("That document has no file.")
            if version.text_state != "done":
                raise Conflict("The text of this file has not been read yet.")
            request_field_extraction(session, principal.tenant_id, version.id)
            return version.number

    # Running (called by the worker)

    @staticmethod
    def _pages(session: Session, version_id: uuid.UUID) -> list[PageText]:
        pages = list(
            session.scalars(select(Page).where(Page.version_id == version_id).order_by(Page.number))
        )
        latest: dict[uuid.UUID, OcrResult] = {}
        if pages:
            for row in session.scalars(
                select(OcrResult)
                .where(OcrResult.page_id.in_([page.id for page in pages]))
                .order_by(OcrResult.created_at, OcrResult.id)
            ):
                latest[row.page_id] = row
        result: list[PageText] = []
        for page in pages:
            read = latest.get(page.id)
            if read is None or page.is_blank:
                continue
            result.append(
                PageText(
                    page_id=page.id,
                    number=page.number,
                    text=read.text,
                    words=list(read.boxes or []),
                    width=page.width,
                    height=page.height,
                    confidence=None if read.engine == NATIVE else read.confidence,
                )
            )
        return result

    def run(self, db: Database, ctx: JobContext) -> None:
        version_id = uuid.UUID(ctx.payload["version_id"])
        tenant_id = ctx.tenant_id
        with db.tenant_session(tenant_id) as session:
            version = session.get(DocumentVersion, version_id)
            if version is None:
                return
            document = session.scalar(
                select(Document).where(Document.id == version.document_id).with_for_update()
            )
            # A newer version has replaced this one while the job waited.
            if document is None or document.current_version_id != version.id:
                return
            pages = self._pages(session, version_id)
            if not pages:
                return
            target, confidence, applied_type = self._classify(session, document, version, pages)
            found_count = 0
            filled: list[str] = []
            if target is not None and target.current_schema_version_id is not None:
                found_count, filled = self._extract(
                    session, document, version, target, pages, tenant_id
                )
            self._audit.record(
                session,
                tenant_id,
                "document.fields_suggested",
                actor=AuditActor(),
                object_type="document",
                object_id=str(document.id),
                details={
                    "version": version.number,
                    "type": target.name if target else None,
                    "type_applied": applied_type,
                    "classification_confidence": confidence,
                    "found": found_count,
                    "filled": filled,
                },
            )

    @staticmethod
    def _classify(
        session: Session, document: Document, version: DocumentVersion, pages: list[PageText]
    ) -> tuple[DocumentType | None, float, bool]:
        """Proposes a type, and uses it when it is already the document type or trusted enough."""
        text = "\n".join(page.text for page in pages)
        candidates = list(
            session.scalars(select(DocumentType).where(DocumentType.status == "active"))
        )
        ranked = classify(text, [(item.id, item.name, item.keywords or []) for item in candidates])
        confidence = classification_confidence(ranked)
        best = next((item for item in candidates if ranked and item.id == ranked[0].type_id), None)
        record = session.scalar(
            select(ClassificationResult).where(ClassificationResult.version_id == version.id)
        )
        if record is None:
            record = ClassificationResult(tenant_id=version.tenant_id, version_id=version.id)
            session.add(record)
        record.document_type_id = best.id if best else None
        record.method = "keywords"
        record.confidence = confidence
        record.scores = [
            {
                "type_id": str(item.type_id),
                "name": item.name,
                "score": item.score,
                "matched": item.matched,
            }
            for item in ranked[:TOP_SCORES]
        ]
        if document.document_type_id:
            return session.get(DocumentType, document.document_type_id), confidence, False
        if best is None or confidence < best.review_threshold:
            return None, confidence, False
        if confidence >= best.auto_accept_threshold and best.current_schema_version_id:
            document.document_type_id = best.id
            document.schema_version_id = best.current_schema_version_id
            document.row_version += 1
            return best, confidence, True
        return best, confidence, False

    @staticmethod
    def _extract(
        session: Session,
        document: Document,
        version: DocumentVersion,
        target: DocumentType,
        pages: list[PageText],
        tenant_id: uuid.UUID,
    ) -> tuple[int, list[str]]:
        """Finds the best value for each field with rules. Returns how many, and what was filled."""
        assert target.current_schema_version_id is not None
        views = IndexingService._fields_of(session, target.current_schema_version_id)
        held = {
            row.field_id
            for row in session.scalars(
                select(IndexValue).where(IndexValue.document_id == document.id)
            )
        }
        writable = (
            document.document_type_id == target.id
            and document.schema_version_id == target.current_schema_version_id
        )
        found_count = 0
        filled: list[str] = []
        for view in views:
            rules = view.field.extraction or {}
            if not rules.get("labels") and not rules.get("pattern"):
                continue
            kind = view.field.data_type
            choices = [(choice.value, choice.label) for choice in view.choices]
            chosen: _Option | None = None
            for hit in find_values(kind, rules, pages, choices):
                as_text = to_value_text(kind, hit.raw, choices)
                value: Any = None
                if as_text is not None:
                    try:
                        value = coerce(view, as_text)
                    except ValueError:
                        value = None
                valid = value is not None
                placed = locate(hit)
                option = _Option(
                    score(hit, placed, valid),
                    hit,
                    placed,
                    value_text(kind, value) if valid else as_text,
                    valid,
                    value,
                )
                # The most confident wins, and the earliest page when they tie.
                if chosen is None or (option.confidence, -hit.page.number) > (
                    chosen.confidence,
                    -chosen.hit.page.number,
                ):
                    chosen = option
            if chosen is None:
                continue
            found_count += 1
            row = session.scalar(
                select(ExtractedField).where(
                    ExtractedField.version_id == version.id,
                    ExtractedField.field_id == view.field.id,
                )
            )
            if row is not None and row.state != "suggested":
                continue  # a person has already dealt with this one
            if row is None:
                row = ExtractedField(
                    tenant_id=tenant_id, version_id=version.id, field_id=view.field.id
                )
                session.add(row)
            row.raw_value = chosen.hit.raw
            row.normalized_value = chosen.rendered if isinstance(chosen.rendered, str) else None
            row.confidence = chosen.confidence
            row.method = chosen.hit.method
            row.page_id = chosen.hit.page.page_id
            row.char_start, row.char_end = chosen.hit.start, chosen.hit.end
            row.bbox = {
                "page_width": chosen.hit.page.width,
                "page_height": chosen.hit.page.height,
                "boxes": chosen.placed.boxes,
            }
            row.validation_state = "valid" if chosen.valid else "invalid"
            row.state = "suggested"
            if (
                writable
                and chosen.valid
                and chosen.confidence >= target.auto_accept_threshold
                and view.field.id not in held
            ):
                session.add(
                    IndexValue(
                        tenant_id=tenant_id,
                        document_id=document.id,
                        field_id=view.field.id,
                        validated_by=None,
                        **_columns(kind, chosen.value),
                    )
                )
                row.state = "accepted"
                filled.append(view.field.key)
        return found_count, filled
