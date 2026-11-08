"""Document types, their fields, and the values documents hold (F06).

Rules that hold throughout:
- A type's fields live in immutable schema versions. Changing the fields publishes a new version
  and never rewrites what was recorded. A document keeps pointing at the version its values were
  written against until someone saves it again, which moves it to the type's current version.
- Making or changing types needs the ``document_types.manage`` right. Seeing a document's values
  needs ``read`` on the document, and changing them needs ``edit_metadata``.
- A value is checked against its field before it is stored, in the column that fits its type.
  A missing required value does not stop a save, because filing happens in steps. The document
  reports what is still missing instead.
- Fields are checked all at once, so a person sees every problem in one go.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.indexing.models import (
    DATA_TYPES,
    DocumentType,
    FieldChoice,
    IndexField,
    IndexSchemaVersion,
    IndexValue,
)
from docuflow.modules.repository.models import Document
from docuflow.modules.repository.service import RepositoryService
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.db import utcnow
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound, ValidationFailed
from docuflow.platform.jobs import enqueue

MANAGE = "document_types.manage"
KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_FIELDS = 40
MAX_CHOICES = 100
MAX_PATTERN = 200
MAX_TEXT = 2000
# A pattern is run on what people type, so it only sees short text and cannot be written to
# backtrack without limit. Python's re has no timeout, which is why these limits exist.
MAX_PATTERN_INPUT = 500
NESTED_REPEAT = re.compile(r"\([^()]*[+*][^()]*\)\s*[+*{]")
TRUE_WORDS = {"true", "yes", "y", "1", "on"}
FALSE_WORDS = {"false", "no", "n", "0", "off"}
ALLOWED_RULES = {
    "text": {"pattern", "min_length", "max_length"},
    "number": {"min", "max"},
    "date": {"min", "max"},
    "boolean": set(),
    "choice": set(),
}
AUDIT_VALUE_LIMIT = 200
MAX_LABELS = 10
MAX_LABEL = 60
MAX_KEYWORDS = 20


@dataclass(frozen=True)
class ChoiceInput:
    value: str
    label: str


@dataclass(frozen=True)
class FieldInput:
    key: str
    label: str
    data_type: str
    required: bool = False
    read_only: bool = False
    default_value: str | None = None
    validation: dict[str, Any] = field(default_factory=dict)
    choices: tuple[ChoiceInput, ...] = ()
    extraction: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FieldView:
    field: IndexField
    choices: list[FieldChoice]


@dataclass(frozen=True)
class TypeView:
    type: DocumentType
    version: IndexSchemaVersion | None
    fields: list[FieldView]


@dataclass(frozen=True)
class IndexedField:
    view: FieldView
    value: Any


@dataclass(frozen=True)
class IndexView:
    document: Document
    type: DocumentType | None
    schema_version: int | None
    current_version: int | None
    fields: list[IndexedField]
    missing_required: list[str]

    @property
    def outdated(self) -> bool:
        return (
            self.type is not None
            and self.schema_version is not None
            and self.schema_version != self.current_version
        )

    @property
    def complete(self) -> bool:
        return self.type is not None and not self.missing_required


# Checking definitions and values


def _is_number(raw: Any) -> bool:
    return isinstance(raw, int | float | Decimal | str) and not isinstance(raw, bool)


def parse_number(raw: Any) -> Decimal:
    if not _is_number(raw):
        raise ValueError("Enter a number.")
    try:
        number = Decimal(str(raw).strip())
    except InvalidOperation as exc:
        raise ValueError("Enter a number.") from exc
    if not number.is_finite():
        raise ValueError("Enter a number.")
    if abs(number) >= Decimal(10) ** 14:
        raise ValueError("That number is too large.")
    return number.quantize(Decimal("0.000001"))


def parse_date(raw: Any) -> date:
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise ValueError("Enter a date as year-month-day, for example 2026-11-30.") from exc


def render_number(number: Decimal) -> str:
    return format(number.normalize(), "f")


def check_definition(item: FieldInput) -> None:
    """Raises ValueError with a message about one field's definition."""
    if not KEY.match(item.key):
        raise ValueError(
            "The key must start with a lowercase letter and use only lowercase letters, "
            "digits and underscores, at most 40 characters."
        )
    if not item.label.strip() or len(item.label) > 120:
        raise ValueError("The label must be between 1 and 120 characters.")
    if item.data_type not in DATA_TYPES:
        raise ValueError("The type must be text, number, date, boolean or choice.")
    unknown = set(item.validation) - ALLOWED_RULES[item.data_type]
    if unknown:
        raise ValueError(
            f"{', '.join(sorted(unknown))} does not apply to a {item.data_type} field."
        )
    rules = item.validation
    if item.data_type == "text":
        pattern = rules.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN:
                raise ValueError(f"A pattern must be 1 to {MAX_PATTERN} characters.")
            if NESTED_REPEAT.search(pattern):
                raise ValueError("That pattern repeats a repeat, which can hang. Simplify it.")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"The pattern is not valid: {exc}.") from exc
        for name in ("min_length", "max_length"):
            if name in rules and not (
                isinstance(rules[name], int) and 0 <= rules[name] <= MAX_TEXT
            ):
                raise ValueError(f"{name} must be a whole number from 0 to {MAX_TEXT}.")
        low, high = rules.get("min_length"), rules.get("max_length")
        if low is not None and high is not None and low > high:
            raise ValueError("min_length cannot be more than max_length.")
    elif item.data_type in ("number", "date"):
        bounds: list[Any] = []
        for name in ("min", "max"):
            if name in rules:
                try:
                    bounds.append(
                        parse_number(rules[name])
                        if item.data_type == "number"
                        else parse_date(rules[name])
                    )
                except ValueError as exc:
                    raise ValueError(f"{name}: {exc}") from exc
        if len(bounds) == 2 and bounds[0] > bounds[1]:
            raise ValueError("min cannot be more than max.")
    if item.data_type == "choice":
        if not item.choices:
            raise ValueError("A choice field needs at least one choice.")
        if len(item.choices) > MAX_CHOICES:
            raise ValueError(f"A choice field can have at most {MAX_CHOICES} choices.")
        values = [choice.value for choice in item.choices]
        if len(set(values)) != len(values):
            raise ValueError("Choices must have different values.")
        for choice in item.choices:
            if not choice.value.strip() or len(choice.value) > 120 or not choice.label.strip():
                raise ValueError("Every choice needs a value (up to 120 characters) and a label.")
    elif item.choices:
        raise ValueError("Only a choice field has choices.")
    check_extraction(item.extraction)
    if item.default_value not in (None, ""):
        try:
            coerce(_view_of(item), item.default_value)
        except ValueError as exc:
            raise ValueError(f"The default value is not valid: {exc}") from exc


def request_search_row(session: Session, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """Queues rebuilding the document's search row. The job type is named, not imported."""
    enqueue(
        session,
        tenant_id,
        "search.index_document",
        f"search:{document_id}:{uuid.uuid4().hex}",
        queue="index",
        payload={"document_id": str(document_id)},
    )


def check_pattern(pattern: Any) -> None:
    """Raises ValueError unless the pattern is safe and valid. Used for rules and for fields."""
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN:
        raise ValueError(f"A pattern must be 1 to {MAX_PATTERN} characters.")
    if NESTED_REPEAT.search(pattern):
        raise ValueError("That pattern repeats a repeat, which can hang. Simplify it.")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"The pattern is not valid: {exc}.") from exc


def check_extraction(rules: dict[str, Any]) -> None:
    """Raises ValueError about the rules that tell the finder where a field's value is."""
    unknown = set(rules) - {"labels", "pattern"}
    if unknown:
        raise ValueError(
            f"Extraction rules can only be labels and a pattern, not {sorted(unknown)}."
        )
    labels = rules.get("labels", [])
    if not isinstance(labels, list) or len(labels) > MAX_LABELS:
        raise ValueError(f"Give at most {MAX_LABELS} labels as a list.")
    for label in labels:
        if not isinstance(label, str) or not label.strip() or len(label) > MAX_LABEL:
            raise ValueError(f"Each label must be 1 to {MAX_LABEL} characters.")
    if "pattern" in rules:
        check_pattern(rules["pattern"])


def check_keywords(keywords: list[str]) -> list[str]:
    cleaned = [word.strip() for word in keywords if word.strip()]
    if len(cleaned) > MAX_KEYWORDS or any(len(word) > MAX_LABEL for word in cleaned):
        raise DomainError(
            f"Give at most {MAX_KEYWORDS} keywords of up to {MAX_LABEL} characters each."
        )
    return list(dict.fromkeys(cleaned))


def check_thresholds(auto_accept: float, review: float) -> None:
    if not 0 < review <= auto_accept <= 1:
        raise DomainError(
            "The review level must be above 0 and no higher than the fill-in-at-once level, "
            "which must be 1 or less."
        )


def value_text(kind: str, value: Any) -> str | None:
    """A value as plain text, the form suggestions are stored and compared in."""
    if value is None:
        return None
    if kind == "boolean":
        return "yes" if value else "no"
    rendered = render_value(kind, value)
    return rendered if isinstance(rendered, str) else str(rendered)


def _view_of(item: FieldInput) -> FieldView:
    """A throwaway view of an input, so a default can be checked like any other value."""
    stored = IndexField(
        key=item.key,
        label=item.label,
        data_type=item.data_type,
        required=item.required,
        read_only=item.read_only,
        validation=item.validation,
        default_value=item.default_value or None,
        extraction=item.extraction,
    )
    return FieldView(
        stored,
        [FieldChoice(value=c.value, label=c.label, position=i) for i, c in enumerate(item.choices)],
    )


def coerce(view: FieldView, raw: Any) -> Any:
    """The value in its proper type, or None when empty. Raises ValueError with a message."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    kind, rules = view.field.data_type, view.field.validation
    if kind == "text":
        if not isinstance(raw, str | int | float | Decimal) or isinstance(raw, bool):
            raise ValueError("Enter text.")
        text = str(raw).strip()
        if len(text) > MAX_TEXT:
            raise ValueError(f"Use at most {MAX_TEXT} characters.")
        low, high = rules.get("min_length"), rules.get("max_length")
        if isinstance(low, int) and len(text) < low:
            raise ValueError(f"Use at least {low} characters.")
        if isinstance(high, int) and len(text) > high:
            raise ValueError(f"Use at most {high} characters.")
        pattern = rules.get("pattern")
        if isinstance(pattern, str):
            if len(text) > MAX_PATTERN_INPUT or not re.fullmatch(pattern, text):
                raise ValueError("That does not match the expected format.")
        return text
    if kind == "number":
        number = parse_number(raw)
        low, high = rules.get("min"), rules.get("max")
        if low is not None and number < parse_number(low):
            raise ValueError(f"Use {render_number(parse_number(low))} or more.")
        if high is not None and number > parse_number(high):
            raise ValueError(f"Use {render_number(parse_number(high))} or less.")
        return number
    if kind == "date":
        day = parse_date(raw)
        low, high = rules.get("min"), rules.get("max")
        if low is not None and day < parse_date(low):
            raise ValueError(f"Use {parse_date(low).isoformat()} or later.")
        if high is not None and day > parse_date(high):
            raise ValueError(f"Use {parse_date(high).isoformat()} or earlier.")
        return day
    if kind == "boolean":
        if isinstance(raw, bool):
            return raw
        word = str(raw).strip().lower()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
        raise ValueError("Choose yes or no.")
    allowed = {choice.value for choice in view.choices}
    if str(raw).strip() not in allowed:
        raise ValueError("Choose one of the listed values.")
    return str(raw).strip()


def render_value(kind: str, value: Any) -> Any:
    """A value in the form the API returns it: numbers as exact text, dates as ISO text."""
    if value is None:
        return None
    if kind == "number":
        return render_number(value)
    if kind == "date":
        return value.isoformat()
    return value


def _short(value: Any, kind: str) -> Any:
    rendered = render_value(kind, value)
    return rendered[:AUDIT_VALUE_LIMIT] if isinstance(rendered, str) else rendered


def _columns(kind: str, value: Any) -> dict[str, Any]:
    return {
        "value_text": value if kind in ("text", "choice") else None,
        "value_number": value if kind == "number" else None,
        "value_date": value if kind == "date" else None,
        "value_bool": value if kind == "boolean" else None,
    }


def _stored(row: IndexValue, kind: str) -> Any:
    return {
        "text": row.value_text,
        "choice": row.value_text,
        "number": row.value_number,
        "date": row.value_date,
        "boolean": row.value_bool,
    }[kind]


class IndexingService:
    def __init__(self, audit: AuditLog, policy: PolicyService, repo: RepositoryService) -> None:
        self._audit = audit
        self._policy = policy
        self._repo = repo

    # Helpers

    def _actor(self, principal: Principal) -> AuditActor:
        return AuditActor(principal.user_id, "user" if principal.kind == "session" else "api_key")

    def _record(
        self,
        session: Session,
        principal: Principal,
        action: str,
        object_type: str,
        object_id: uuid.UUID,
        details: dict[str, Any],
    ) -> None:
        self._audit.record(
            session,
            principal.tenant_id,
            action,
            actor=self._actor(principal),
            object_type=object_type,
            object_id=str(object_id),
            details=details,
        )

    def _require_manage(self, session: Session, principal: Principal) -> None:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        if MANAGE not in held:
            raise Forbidden("You do not have permission to do that.", details={"required": MANAGE})

    @staticmethod
    def _check_fields(inputs: list[FieldInput]) -> None:
        if not inputs:
            raise DomainError("A document type needs at least one field.")
        if len(inputs) > MAX_FIELDS:
            raise DomainError(f"A document type can have at most {MAX_FIELDS} fields.")
        problems: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in inputs:
            try:
                if item.key in seen:
                    raise ValueError("Two fields have this key.")
                seen.add(item.key)
                check_definition(item)
            except ValueError as exc:
                problems.append({"field": item.key or "(no key)", "message": str(exc)})
        if problems:
            raise ValidationFailed("Some fields are not valid.", problems)

    @staticmethod
    def _fields_of(session: Session, version_id: uuid.UUID | None) -> list[FieldView]:
        if version_id is None:
            return []
        fields = list(
            session.scalars(
                select(IndexField)
                .where(IndexField.schema_version_id == version_id)
                .order_by(IndexField.position, IndexField.key)
            )
        )
        choices: dict[uuid.UUID, list[FieldChoice]] = {}
        if fields:
            for choice in session.scalars(
                select(FieldChoice)
                .where(FieldChoice.field_id.in_([item.id for item in fields]))
                .order_by(FieldChoice.position, FieldChoice.value)
            ):
                choices.setdefault(choice.field_id, []).append(choice)
        return [FieldView(item, choices.get(item.id, [])) for item in fields]

    def _view_of_type(self, session: Session, row: DocumentType) -> TypeView:
        version = (
            session.get(IndexSchemaVersion, row.current_schema_version_id)
            if row.current_schema_version_id
            else None
        )
        return TypeView(row, version, self._fields_of(session, row.current_schema_version_id))

    @staticmethod
    def _write_version(
        session: Session, principal: Principal, row: DocumentType, inputs: list[FieldInput]
    ) -> IndexSchemaVersion:
        number = (
            session.scalar(
                select(func.max(IndexSchemaVersion.version)).where(
                    IndexSchemaVersion.document_type_id == row.id
                )
            )
            or 0
        ) + 1
        version = IndexSchemaVersion(
            tenant_id=principal.tenant_id,
            document_type_id=row.id,
            version=number,
            created_by=principal.user_id,
        )
        session.add(version)
        session.flush()
        for position, item in enumerate(inputs):
            stored = IndexField(
                tenant_id=principal.tenant_id,
                schema_version_id=version.id,
                key=item.key,
                label=item.label.strip(),
                data_type=item.data_type,
                position=position,
                required=item.required,
                read_only=item.read_only,
                validation=dict(item.validation),
                default_value=item.default_value or None,
                extraction=dict(item.extraction),
            )
            session.add(stored)
            session.flush()
            for order, choice in enumerate(item.choices):
                session.add(
                    FieldChoice(
                        tenant_id=principal.tenant_id,
                        field_id=stored.id,
                        value=choice.value.strip(),
                        label=choice.label.strip(),
                        position=order,
                    )
                )
        row.current_schema_version_id = version.id
        row.row_version += 1
        session.flush()
        return version

    @staticmethod
    def _signature(views: list[FieldView]) -> list[tuple[Any, ...]]:
        return [
            (
                view.field.key,
                view.field.label,
                view.field.data_type,
                view.field.required,
                view.field.read_only,
                sorted(view.field.validation.items()),
                view.field.default_value or None,
                [(choice.value, choice.label) for choice in view.choices],
                json.dumps(view.field.extraction or {}, sort_keys=True),
            )
            for view in views
        ]

    # Types

    def list_types(self, session: Session, include_archived: bool = False) -> list[TypeView]:
        query = select(DocumentType).order_by(func.lower(DocumentType.name))
        if not include_archived:
            query = query.where(DocumentType.status == "active")
        return [self._view_of_type(session, row) for row in session.scalars(query)]

    def get_type(self, session: Session, type_id: uuid.UUID) -> TypeView:
        row = session.get(DocumentType, type_id)
        if row is None:
            raise NotFound("Document type not found.")
        return self._view_of_type(session, row)

    def create_type(
        self,
        session: Session,
        principal: Principal,
        name: str,
        description: str,
        inputs: list[FieldInput],
        keywords: list[str] | None = None,
        auto_accept_threshold: float = 0.9,
        review_threshold: float = 0.6,
    ) -> TypeView:
        self._require_manage(session, principal)
        name = name.strip()
        if not name or len(name) > 120:
            raise DomainError("Names must be between 1 and 120 characters.")
        self._check_fields(inputs)
        check_thresholds(auto_accept_threshold, review_threshold)
        row = DocumentType(
            tenant_id=principal.tenant_id,
            name=name,
            description=description.strip()[:1000],
            keywords=check_keywords(keywords or []),
            auto_accept_threshold=auto_accept_threshold,
            review_threshold=review_threshold,
            created_by=principal.user_id,
        )
        session.add(row)
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError as exc:
            raise Conflict("A document type with that name already exists.") from exc
        version = self._write_version(session, principal, row, inputs)
        self._record(
            session,
            principal,
            "document_type.created",
            "document_type",
            row.id,
            {"name": name, "version": version.version, "fields": [item.key for item in inputs]},
        )
        return self._view_of_type(session, row)

    def new_version(
        self,
        session: Session,
        principal: Principal,
        type_id: uuid.UUID,
        inputs: list[FieldInput],
        row_version: int,
    ) -> TypeView:
        self._require_manage(session, principal)
        row = session.scalar(
            select(DocumentType).where(DocumentType.id == type_id).with_for_update()
        )
        if row is None:
            raise NotFound("Document type not found.")
        if row.row_version != row_version:
            raise Conflict("This type was changed by someone else. Reload and try again.")
        if row.status != "active":
            raise Conflict("That document type is archived.")
        self._check_fields(inputs)
        current = self._fields_of(session, row.current_schema_version_id)
        same = self._signature(current) == self._signature([_view_of(item) for item in inputs])
        if same:
            raise Conflict("The fields are the same as the current version.")
        previous = {view.field.key for view in current}
        version = self._write_version(session, principal, row, inputs)
        keys = [item.key for item in inputs]
        self._record(
            session,
            principal,
            "document_type.version_published",
            "document_type",
            row.id,
            {
                "version": version.version,
                "added": sorted(set(keys) - previous),
                "removed": sorted(previous - set(keys)),
                "fields": keys,
            },
        )
        return self._view_of_type(session, row)

    def update_type(
        self,
        session: Session,
        principal: Principal,
        type_id: uuid.UUID,
        row_version: int,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
        keywords: list[str] | None = None,
        auto_accept_threshold: float | None = None,
        review_threshold: float | None = None,
    ) -> TypeView:
        self._require_manage(session, principal)
        row = session.scalar(
            select(DocumentType).where(DocumentType.id == type_id).with_for_update()
        )
        if row is None:
            raise NotFound("Document type not found.")
        if row.row_version != row_version:
            raise Conflict("This type was changed by someone else. Reload and try again.")
        changes: dict[str, Any] = {}
        if name is not None and name.strip() != row.name:
            clean = name.strip()
            if not clean or len(clean) > 120:
                raise DomainError("Names must be between 1 and 120 characters.")
            changes["name"] = [row.name, clean]
            row.name = clean
        if description is not None and description.strip() != row.description:
            changes["description"] = True
            row.description = description.strip()[:1000]
        if status is not None and status != row.status:
            if status not in ("active", "archived"):
                raise DomainError("The status must be active or archived.")
            changes["status"] = [row.status, status]
            row.status = status
        if keywords is not None:
            cleaned = check_keywords(keywords)
            if cleaned != list(row.keywords or []):
                changes["keywords"] = cleaned
                row.keywords = cleaned
        auto = row.auto_accept_threshold if auto_accept_threshold is None else auto_accept_threshold
        review = row.review_threshold if review_threshold is None else review_threshold
        if (auto, review) != (row.auto_accept_threshold, row.review_threshold):
            check_thresholds(auto, review)
            changes["thresholds"] = [auto, review]
            row.auto_accept_threshold, row.review_threshold = auto, review
        if changes:
            row.row_version += 1
            try:
                with session.begin_nested():
                    session.flush()
            except IntegrityError as exc:
                raise Conflict("A document type with that name already exists.") from exc
            self._record(
                session, principal, "document_type.updated", "document_type", row.id, changes
            )
        return self._view_of_type(session, row)

    # A document's values

    def _values_by_key(self, session: Session, document_id: uuid.UUID) -> dict[str, Any]:
        """What the document holds now, by field key, in each value's own type."""
        rows = session.execute(
            select(IndexValue, IndexField)
            .join(IndexField, IndexField.id == IndexValue.field_id)
            .where(IndexValue.document_id == document_id)
        )
        return {field_row.key: _stored(value, field_row.data_type) for value, field_row in rows}

    def _build_view(self, session: Session, document: Document, held: dict[str, Any]) -> IndexView:
        if document.document_type_id is None:
            return IndexView(document, None, None, None, [], [])
        row = session.get(DocumentType, document.document_type_id)
        assert row is not None
        current = self._fields_of(session, row.current_schema_version_id)
        own = (
            session.get(IndexSchemaVersion, document.schema_version_id)
            if document.schema_version_id
            else None
        )
        current_version = session.get(IndexSchemaVersion, row.current_schema_version_id)
        items: list[IndexedField] = []
        missing: list[str] = []
        for view in current:
            try:
                value = coerce(view, held.get(view.field.key))
            except ValueError:
                value = None  # a value from an older version that no longer fits
            items.append(IndexedField(view, value))
            if view.field.required and value is None:
                missing.append(view.field.key)
        return IndexView(
            document,
            row,
            own.version if own else None,
            current_version.version if current_version else None,
            items,
            missing,
        )

    def get_index(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> IndexView:
        document = self._repo.get_document(session, principal, document_id)
        return self._build_view(session, document, self._values_by_key(session, document_id))

    def set_index(
        self,
        session: Session,
        principal: Principal,
        document_id: uuid.UUID,
        type_id: uuid.UUID | None,
        values: dict[str, Any],
    ) -> IndexView:
        """Sets the type and replaces the values the person may edit. Read-only fields stay."""
        document = self._repo.document_for_change(
            session, principal, document_id, "edit_metadata", lock=True
        )
        held = self._values_by_key(session, document_id)
        if type_id is None:
            session.execute(delete(IndexValue).where(IndexValue.document_id == document_id))
            changed = document.document_type_id is not None
            document.document_type_id = None
            document.schema_version_id = None
            if changed:
                document.row_version += 1
                self._record(
                    session,
                    principal,
                    "document.indexed",
                    "document",
                    document_id,
                    {"type": None, "removed": sorted(held)},
                )
            return self._build_view(session, document, {})

        row = session.get(DocumentType, type_id)
        if row is None:
            raise NotFound("Document type not found.")
        newly_typed = document.document_type_id != row.id
        if row.status != "active" and newly_typed:
            raise Conflict("That document type is archived.")
        views = self._fields_of(session, row.current_schema_version_id)
        by_key = {view.field.key: view for view in views}
        problems: list[dict[str, str]] = [
            {"field": key, "message": "This document type has no such field."}
            for key in values
            if key not in by_key
        ]
        typed: dict[str, Any] = {}
        for view in views:
            key = view.field.key
            if view.field.read_only:
                # The system fills these. Asking for the value they already have is fine.
                if key in values:
                    try:
                        asked = coerce(view, values[key])
                    except ValueError:
                        asked = object()
                    if asked != held.get(key):
                        problems.append(
                            {"field": key, "message": "This field cannot be changed here."}
                        )
                if held.get(key) is not None:
                    typed[key] = held[key]
                continue
            if key in values:
                raw = values[key]
            elif newly_typed and view.field.default_value:
                raw = view.field.default_value
            else:
                raw = None
            try:
                value = coerce(view, raw)
            except ValueError as exc:
                problems.append({"field": key, "message": str(exc)})
                continue
            if value is not None:
                typed[key] = value
        if problems:
            raise ValidationFailed("Some values are not valid.", problems)

        session.execute(delete(IndexValue).where(IndexValue.document_id == document_id))
        session.flush()
        now = utcnow()
        for key, value in typed.items():
            view = by_key[key]
            session.add(
                IndexValue(
                    tenant_id=principal.tenant_id,
                    document_id=document_id,
                    field_id=view.field.id,
                    validated_by=principal.user_id,
                    updated_at=now,
                    **_columns(view.field.data_type, value),
                )
            )
        document.document_type_id = row.id
        document.schema_version_id = row.current_schema_version_id
        document.row_version += 1
        session.flush()

        changes: dict[str, list[Any]] = {}
        for view in views:
            key = view.field.key
            before = held.get(key)
            try:
                before = coerce(view, before)
            except ValueError:
                before = None
            after = typed.get(key)
            if before != after:
                kind = view.field.data_type
                changes[key] = [_short(before, kind), _short(after, kind)]
        from docuflow.modules.indexing.suggest import settle_suggestions

        request_search_row(session, principal.tenant_id, document.id)

        settle_suggestions(
            session,
            self._audit,
            principal,
            document,
            {
                view.field.key: value_text(view.field.data_type, typed.get(view.field.key))
                for view in views
            },
        )
        version = session.get(IndexSchemaVersion, row.current_schema_version_id)
        self._record(
            session,
            principal,
            "document.indexed",
            "document",
            document_id,
            {
                "type": str(row.id),
                "schema_version": version.version if version else None,
                "changes": changes,
            },
        )
        return self._build_view(session, document, typed)
