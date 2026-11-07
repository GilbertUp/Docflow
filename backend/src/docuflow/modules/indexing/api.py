"""Indexing HTTP API: document types, and the values a document holds."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from docuflow.modules.identity.api import Ctx, User_
from docuflow.modules.indexing.schemas import (
    ChoiceModel,
    CreateTypeRequest,
    FieldModel,
    IndexFieldOut,
    IndexOut,
    NewVersionRequest,
    SetIndexRequest,
    TypeOut,
    UpdateTypeRequest,
)
from docuflow.modules.indexing.service import (
    ChoiceInput,
    FieldInput,
    FieldView,
    IndexingService,
    IndexView,
    TypeView,
    render_value,
)
from docuflow.platform.errors import Conflict

router = APIRouter(prefix="/api/v1", tags=["indexing"])


def get_indexing(request: Request) -> IndexingService:
    service: IndexingService = request.app.state.indexing
    return service


Indexing = Annotated[IndexingService, Depends(get_indexing)]


def _inputs(fields: list[FieldModel]) -> list[FieldInput]:
    return [
        FieldInput(
            key=item.key,
            label=item.label,
            data_type=item.data_type,
            required=item.required,
            read_only=item.read_only,
            default_value=item.default_value,
            validation=item.validation,
            choices=tuple(ChoiceInput(c.value, c.label) for c in item.choices),
        )
        for item in fields
    ]


def _field(view: FieldView) -> FieldModel:
    stored = view.field
    return FieldModel(
        key=stored.key,
        label=stored.label,
        data_type=stored.data_type,
        required=stored.required,
        read_only=stored.read_only,
        default_value=stored.default_value,
        validation=dict(stored.validation),
        choices=[ChoiceModel(value=c.value, label=c.label) for c in view.choices],
    )


def _type(view: TypeView) -> TypeOut:
    row = view.type
    return TypeOut(
        id=row.id,
        name=row.name,
        description=row.description,
        status=row.status,
        row_version=row.row_version,
        version=view.version.version if view.version else None,
        fields=[_field(item) for item in view.fields],
    )


def _index(view: IndexView) -> IndexOut:
    return IndexOut(
        document_id=view.document.id,
        document_type_id=view.type.id if view.type else None,
        type_name=view.type.name if view.type else None,
        schema_version=view.schema_version,
        current_version=view.current_version,
        outdated=view.outdated,
        complete=view.complete,
        missing_required=view.missing_required,
        row_version=view.document.row_version,
        fields=[
            IndexFieldOut(
                **_field(item.view).model_dump(),
                value=render_value(item.view.field.data_type, item.value),
            )
            for item in view.fields
        ],
    )


@router.get("/document-types")
def list_types(
    ctx: Ctx, indexing: Indexing, who: User_, include_archived: Annotated[bool, Query()] = False
) -> list[TypeOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [_type(view) for view in indexing.list_types(session, include_archived)]


@router.post("/document-types", status_code=201)
def create_type(body: CreateTypeRequest, ctx: Ctx, indexing: Indexing, who: User_) -> TypeOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _type(
            indexing.create_type(session, who, body.name, body.description, _inputs(body.fields))
        )


@router.get("/document-types/{type_id}")
def get_type(type_id: uuid.UUID, ctx: Ctx, indexing: Indexing, who: User_) -> TypeOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _type(indexing.get_type(session, type_id))


@router.post("/document-types/{type_id}/versions", status_code=201)
def publish_version(
    type_id: uuid.UUID, body: NewVersionRequest, ctx: Ctx, indexing: Indexing, who: User_
) -> TypeOut:
    """Publishes a new set of fields. Documents already typed keep the version they have."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _type(
            indexing.new_version(session, who, type_id, _inputs(body.fields), body.row_version)
        )


@router.patch("/document-types/{type_id}")
def update_type(
    type_id: uuid.UUID, body: UpdateTypeRequest, ctx: Ctx, indexing: Indexing, who: User_
) -> TypeOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _type(
            indexing.update_type(
                session,
                who,
                type_id,
                body.row_version,
                name=body.name,
                description=body.description,
                status=body.status,
            )
        )


@router.get("/documents/{document_id}/index")
def get_index(document_id: uuid.UUID, ctx: Ctx, indexing: Indexing, who: User_) -> IndexOut:
    """The document's type and field values, laid out on the type's current fields."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _index(indexing.get_index(session, who, document_id))


@router.put("/documents/{document_id}/index")
def set_index(
    document_id: uuid.UUID, body: SetIndexRequest, ctx: Ctx, indexing: Indexing, who: User_
) -> IndexOut:
    """Sets the type and replaces the editable values. Every problem comes back at once."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        if body.row_version is not None:
            current = indexing.get_index(session, who, document_id)
            if current.document.row_version != body.row_version:
                raise Conflict("This document was changed by someone else. Reload and try again.")
        return _index(
            indexing.set_index(session, who, document_id, body.document_type_id, body.values)
        )
