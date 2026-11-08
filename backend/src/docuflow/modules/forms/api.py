"""Forms HTTP API: definitions, filling in, and reading what was submitted."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from docuflow.modules.forms.schemas import (
    ChoiceModel,
    CreateFormRequest,
    FormFieldModel,
    FormOut,
    NewFormVersionRequest,
    SubmissionOut,
    SubmitRequest,
    UpdateFormRequest,
)
from docuflow.modules.forms.service import FormService, FormView, SubmissionView, input_of
from docuflow.modules.identity.api import Ctx, User_
from docuflow.modules.indexing.service import ChoiceInput, FieldInput

router = APIRouter(prefix="/api/v1", tags=["forms"])


def get_forms(request: Request) -> FormService:
    service: FormService = request.app.state.forms
    return service


Forms = Annotated[FormService, Depends(get_forms)]


def _inputs(fields: list[FormFieldModel]) -> list[FieldInput]:
    return [
        FieldInput(
            key=item.key,
            label=item.label,
            data_type=item.data_type,
            required=item.required,
            default_value=item.default_value,
            validation=item.validation,
            choices=tuple(ChoiceInput(c.value, c.label) for c in item.choices),
        )
        for item in fields
    ]


def _form(view: FormView) -> FormOut:
    fields: list[FormFieldModel] = []
    for stored in view.version.fields:
        item = input_of(stored)
        fields.append(
            FormFieldModel(
                key=item.key,
                label=item.label,
                data_type=item.data_type,
                required=item.required,
                default_value=item.default_value,
                validation=item.validation,
                choices=[ChoiceModel(value=c.value, label=c.label) for c in item.choices],
            )
        )
    return FormOut(
        id=view.form.id,
        name=view.form.name,
        description=view.form.description,
        status=view.form.status,
        row_version=view.form.row_version,
        version=view.version.version,
        fields=fields,
    )


def _submission(view: SubmissionView) -> SubmissionOut:
    row = view.submission
    return SubmissionOut(
        id=row.id,
        form_name=view.form_name,
        form_version=view.form_version,
        submitter_id=row.submitter_id,
        submitter_name=view.submitter_name,
        document_id=row.document_id,
        created_at=row.created_at,
        labels=view.labels,
        data=dict(row.data),
    )


@router.get("/forms")
def list_forms(ctx: Ctx, forms: Forms, who: User_, include_archived: bool = False) -> list[FormOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_form(v) for v in forms.all_forms(session, include_archived)]


@router.post("/forms", status_code=201)
def create_form(body: CreateFormRequest, ctx: Ctx, forms: Forms, who: User_) -> FormOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _form(forms.create(session, who, body.name, body.description, _inputs(body.fields)))


@router.get("/forms/{form_id}")
def get_form(form_id: uuid.UUID, ctx: Ctx, forms: Forms, who: User_) -> FormOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _form(forms.get(session, form_id))


@router.post("/forms/{form_id}/versions", status_code=201)
def publish_form_version(
    form_id: uuid.UUID, body: NewFormVersionRequest, ctx: Ctx, forms: Forms, who: User_
) -> FormOut:
    """Publishes new fields. Earlier submissions keep the version they were made on."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _form(
            forms.new_version(session, who, form_id, body.row_version, _inputs(body.fields))
        )


@router.patch("/forms/{form_id}")
def update_form(
    form_id: uuid.UUID, body: UpdateFormRequest, ctx: Ctx, forms: Forms, who: User_
) -> FormOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _form(
            forms.update(
                session, who, form_id, body.row_version, name=body.name, status=body.status
            )
        )


@router.post("/forms/{form_id}/submissions", status_code=201)
def submit_form(
    form_id: uuid.UUID, body: SubmitRequest, ctx: Ctx, forms: Forms, who: User_
) -> SubmissionOut:
    """Submits answers. Every problem comes back at once, by field key."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _submission(forms.submit(session, who, form_id, body.answers, body.document_id))


@router.get("/forms/{form_id}/submissions")
def list_submissions(
    form_id: uuid.UUID,
    ctx: Ctx,
    forms: Forms,
    who: User_,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> list[SubmissionOut]:
    """The caller's own submissions, or all of them for someone who manages forms."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            _submission(v)
            for v in forms.submissions(session, who, form_id, limit=limit, offset=offset)
        ]


@router.get("/form-submissions/{submission_id}")
def get_submission(submission_id: uuid.UUID, ctx: Ctx, forms: Forms, who: User_) -> SubmissionOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _submission(forms.one(session, who, submission_id))


@router.get("/documents/{document_id}/form-submissions")
def document_submissions(
    document_id: uuid.UUID, ctx: Ctx, forms: Forms, who: User_
) -> list[SubmissionOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_submission(v) for v in forms.for_document(session, who, document_id)]
