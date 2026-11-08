"""Forms people fill in, and the answers they give (F12).

Rules that hold throughout:
- A form has the same kinds of field as a document type (text, number, date, yes or no, one of a
  list) and the same checks, so a value means the same thing wherever it is entered.
- Published versions never change. A submission records the version it was made on, so changing
  a form never changes what earlier answers mean.
- Anyone signed in can fill in an active form. A person sees their own submissions. Someone with
  ``forms.manage`` sees all of them.
- A submission may be attached to a document: that needs ``annotate`` on it, and anyone who can
  read the document can then see it there.
- All problems with a submission are reported at once, by field key.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docuflow.modules.forms.models import Form, FormSubmission, FormVersion
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.models import User
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.indexing.service import (
    ChoiceInput,
    FieldInput,
    IndexingService,
    _view_of,
    coerce,
    value_text,
)
from docuflow.modules.repository.service import RepositoryService
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.errors import (
    Conflict,
    DomainError,
    Forbidden,
    NotFound,
    ValidationFailed,
)

MANAGE = "forms.manage"
MAX_NAME = 120
MAX_PAGE = 100


@dataclass(frozen=True)
class FormView:
    form: Form
    version: FormVersion


@dataclass(frozen=True)
class SubmissionView:
    submission: FormSubmission
    form_name: str
    form_version: int
    submitter_name: str
    labels: dict[str, str]


def stored_field(item: FieldInput) -> dict[str, Any]:
    return {
        "key": item.key,
        "label": item.label.strip(),
        "data_type": item.data_type,
        "required": item.required,
        "default_value": item.default_value or None,
        "validation": dict(item.validation),
        "choices": [{"value": c.value, "label": c.label} for c in item.choices],
    }


def input_of(stored: dict[str, Any]) -> FieldInput:
    return FieldInput(
        key=stored["key"],
        label=stored["label"],
        data_type=stored["data_type"],
        required=bool(stored.get("required")),
        default_value=stored.get("default_value"),
        validation=dict(stored.get("validation") or {}),
        choices=tuple(ChoiceInput(c["value"], c["label"]) for c in stored.get("choices") or []),
    )


class FormService:
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

    def _can_manage(self, session: Session, principal: Principal) -> bool:
        held = self._policy.system_permissions(session, principal.tenant_id, principal.user_id)
        return MANAGE in held

    def _require_manage(self, session: Session, principal: Principal) -> None:
        if not self._can_manage(session, principal):
            raise Forbidden("You do not have permission to do that.", details={"required": MANAGE})

    @staticmethod
    def _latest(session: Session, form_id: uuid.UUID) -> FormVersion:
        version = session.scalar(
            select(FormVersion)
            .where(FormVersion.form_id == form_id)
            .order_by(FormVersion.version.desc())
            .limit(1)
        )
        if version is None:
            raise NotFound("Form not found.")
        return version

    # Definitions

    def create(
        self,
        session: Session,
        principal: Principal,
        name: str,
        description: str,
        fields: list[FieldInput],
    ) -> FormView:
        self._require_manage(session, principal)
        name = name.strip()
        if not name or len(name) > MAX_NAME:
            raise DomainError(f"Names must be between 1 and {MAX_NAME} characters.")
        IndexingService._check_fields(fields)
        form = Form(
            tenant_id=principal.tenant_id,
            name=name,
            description=description.strip()[:1000],
            created_by=principal.user_id,
        )
        session.add(form)
        try:
            session.flush()
        except IntegrityError as exc:
            raise Conflict("A form with that name already exists.") from exc
        version = FormVersion(
            tenant_id=principal.tenant_id,
            form_id=form.id,
            version=1,
            fields=[stored_field(f) for f in fields],
            created_by=principal.user_id,
        )
        session.add(version)
        session.flush()
        self._record(
            session,
            principal,
            "form.created",
            "form",
            form.id,
            {"name": name, "fields": len(fields)},
        )
        return FormView(form, version)

    def all_forms(self, session: Session, include_archived: bool = False) -> list[FormView]:
        query = select(Form).order_by(Form.name)
        if not include_archived:
            query = query.where(Form.status == "active")
        return [FormView(row, self._latest(session, row.id)) for row in session.scalars(query)]

    def get(self, session: Session, form_id: uuid.UUID) -> FormView:
        form = session.get(Form, form_id)
        if form is None:
            raise NotFound("Form not found.")
        return FormView(form, self._latest(session, form.id))

    def new_version(
        self,
        session: Session,
        principal: Principal,
        form_id: uuid.UUID,
        row_version: int,
        fields: list[FieldInput],
    ) -> FormView:
        self._require_manage(session, principal)
        form = session.scalar(select(Form).where(Form.id == form_id).with_for_update())
        if form is None:
            raise NotFound("Form not found.")
        if form.row_version != row_version:
            raise Conflict("This form was changed by someone else. Reload and try again.")
        IndexingService._check_fields(fields)
        latest = self._latest(session, form.id)
        stored = [stored_field(f) for f in fields]
        if stored == latest.fields:
            raise Conflict("The fields are the same as the current version.")
        version = FormVersion(
            tenant_id=principal.tenant_id,
            form_id=form.id,
            version=latest.version + 1,
            fields=stored,
            created_by=principal.user_id,
        )
        session.add(version)
        form.row_version += 1
        session.flush()
        before = {f["key"] for f in latest.fields}
        after = {f["key"] for f in stored}
        self._record(
            session,
            principal,
            "form.version_published",
            "form",
            form.id,
            {
                "version": version.version,
                "added": sorted(after - before),
                "removed": sorted(before - after),
            },
        )
        return FormView(form, version)

    def update(
        self,
        session: Session,
        principal: Principal,
        form_id: uuid.UUID,
        row_version: int,
        *,
        name: str | None = None,
        status: str | None = None,
    ) -> FormView:
        self._require_manage(session, principal)
        form = session.scalar(select(Form).where(Form.id == form_id).with_for_update())
        if form is None:
            raise NotFound("Form not found.")
        if form.row_version != row_version:
            raise Conflict("This form was changed by someone else. Reload and try again.")
        changes: dict[str, object] = {}
        if name is not None and name.strip() != form.name:
            clean = name.strip()
            if not clean or len(clean) > MAX_NAME:
                raise DomainError(f"Names must be between 1 and {MAX_NAME} characters.")
            changes["name"] = [form.name, clean]
            form.name = clean
        if status is not None and status != form.status:
            if status not in ("active", "archived"):
                raise DomainError("The status must be active or archived.")
            changes["status"] = [form.status, status]
            form.status = status
        if changes:
            form.row_version += 1
            try:
                session.flush()
            except IntegrityError as exc:
                raise Conflict("A form with that name already exists.") from exc
            self._record(session, principal, "form.updated", "form", form.id, {"changes": changes})
        return FormView(form, self._latest(session, form.id))

    # Submissions

    def submit(
        self,
        session: Session,
        principal: Principal,
        form_id: uuid.UUID,
        answers: dict[str, Any],
        document_id: uuid.UUID | None = None,
    ) -> SubmissionView:
        view = self.get(session, form_id)
        if view.form.status != "active":
            raise Conflict("That form is archived.")
        if document_id is not None:
            document = self._repo.get_document(session, principal, document_id)
            tree = self._policy.tree(session, principal.tenant_id, principal.user_id)
            if not tree.decide(document.acl_anchor_id, "annotate").allowed:
                raise Forbidden(
                    "You do not have permission to do that.", details={"required": "annotate"}
                )
        problems: list[dict[str, str]] = []
        known = {f["key"] for f in view.version.fields}
        problems += [
            {"field": key, "message": "This form has no such field."}
            for key in answers
            if key not in known
        ]
        data: dict[str, Any] = {}
        for stored in view.version.fields:
            key = stored["key"]
            field = input_of(stored)
            raw = answers.get(key)
            try:
                value = coerce(_view_of(field), raw)
            except ValueError as exc:
                problems.append({"field": key, "message": str(exc)})
                continue
            if value is None and field.required:
                problems.append({"field": key, "message": "This is required."})
                continue
            data[key] = value_text(field.data_type, value)
        if problems:
            raise ValidationFailed("Some answers are not valid.", problems)
        row = FormSubmission(
            tenant_id=principal.tenant_id,
            form_version_id=view.version.id,
            submitter_id=principal.user_id,
            document_id=document_id,
            data=data,
        )
        session.add(row)
        session.flush()
        self._record(
            session,
            principal,
            "form.submitted",
            "form",
            view.form.id,
            {
                "submission": str(row.id),
                "version": view.version.version,
                "document": str(document_id) if document_id else None,
            },
        )
        if document_id is not None:
            self._record(
                session,
                principal,
                "document.form_submitted",
                "document",
                document_id,
                {"form": view.form.name, "submission": str(row.id)},
            )
        return self._submission(session, row)

    def _submission(self, session: Session, row: FormSubmission) -> SubmissionView:
        version = session.get(FormVersion, row.form_version_id)
        assert version is not None
        form = session.get(Form, version.form_id)
        assert form is not None
        user = session.get(User, row.submitter_id)
        return SubmissionView(
            row,
            form.name,
            version.version,
            user.display_name if user else "Unknown",
            {f["key"]: f["label"] for f in version.fields},
        )

    def submissions(
        self,
        session: Session,
        principal: Principal,
        form_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[SubmissionView]:
        form = session.get(Form, form_id)
        if form is None:
            raise NotFound("Form not found.")
        query = (
            select(FormSubmission)
            .join(FormVersion, FormVersion.id == FormSubmission.form_version_id)
            .where(FormVersion.form_id == form.id)
            .order_by(FormSubmission.created_at.desc(), FormSubmission.id)
            .limit(min(limit, MAX_PAGE))
            .offset(offset)
        )
        if not self._can_manage(session, principal):
            query = query.where(FormSubmission.submitter_id == principal.user_id)
        return [self._submission(session, row) for row in session.scalars(query)]

    def for_document(
        self, session: Session, principal: Principal, document_id: uuid.UUID
    ) -> list[SubmissionView]:
        document = self._repo.get_document(session, principal, document_id)
        rows = session.scalars(
            select(FormSubmission)
            .where(FormSubmission.document_id == document.id)
            .order_by(FormSubmission.created_at.desc(), FormSubmission.id)
            .limit(MAX_PAGE)
        )
        return [self._submission(session, row) for row in rows]

    def one(
        self, session: Session, principal: Principal, submission_id: uuid.UUID
    ) -> SubmissionView:
        row = session.get(FormSubmission, submission_id)
        if row is None:
            raise NotFound("Submission not found.")
        allowed = row.submitter_id == principal.user_id or self._can_manage(session, principal)
        if not allowed and row.document_id is not None:
            try:
                self._repo.get_document(session, principal, row.document_id)
                allowed = True
            except NotFound:
                allowed = False
        if not allowed:
            raise NotFound("Submission not found.")
        return self._submission(session, row)
