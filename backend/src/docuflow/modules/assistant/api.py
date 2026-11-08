"""The assistant over HTTP: ask a question, see its sources, and set what it may do."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from docuflow.modules.assistant.service import Answer, AssistantService, SettingsView
from docuflow.modules.identity.api import Ctx, User_

router = APIRouter(prefix="/api/v1", tags=["assistant"])


def get_assistant(request: Request) -> AssistantService:
    service: AssistantService = request.app.state.assistant
    return service


Asst = Annotated[AssistantService, Depends(get_assistant)]


class AssistantAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    cabinet_id: uuid.UUID | None = None


class AssistantSourceOut(BaseModel):
    number: int
    document_id: uuid.UUID
    title: str
    cabinet_id: uuid.UUID
    folder_id: uuid.UUID | None
    page: int
    snippet: str


class AssistantAnswerOut(BaseModel):
    # generated: written by an AI service from the sources. extracts: the passages themselves.
    # insufficient_evidence: the documents you can read do not hold the answer.
    mode: str
    answer: str | None
    sources: list[AssistantSourceOut]
    notices: list[str]
    # True when parts of a generated answer were removed because they had no valid source.
    flagged: bool
    query_id: uuid.UUID | None


class AssistantSettingsOut(BaseModel):
    generation_enabled: bool
    excluded_cabinet_ids: list[uuid.UUID]
    provider_configured: bool
    provider: str | None
    model: str | None


class AssistantSetSettingsRequest(BaseModel):
    generation_enabled: bool
    excluded_cabinet_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class AssistantHistoryOut(BaseModel):
    id: uuid.UUID
    question: str
    mode: str
    created_at: datetime


def _answer(answer: Answer) -> AssistantAnswerOut:
    return AssistantAnswerOut(
        mode=answer.mode,
        answer=answer.answer,
        sources=[
            AssistantSourceOut(
                number=s.number,
                document_id=s.document.id,
                title=s.document.title,
                cabinet_id=s.document.cabinet_id,
                folder_id=s.document.folder_id,
                page=s.page,
                snippet=s.snippet,
            )
            for s in answer.sources
        ],
        notices=answer.notices,
        flagged=answer.flagged,
        query_id=answer.query_id,
    )


def _settings(view: SettingsView) -> AssistantSettingsOut:
    return AssistantSettingsOut(
        generation_enabled=view.generation_enabled,
        excluded_cabinet_ids=[uuid.UUID(c) for c in view.excluded_cabinet_ids],
        provider_configured=view.provider_configured,
        provider=view.provider,
        model=view.model,
    )


@router.post("/assistant/ask")
def ask(body: AssistantAskRequest, ctx: Ctx, asst: Asst, who: User_) -> AssistantAnswerOut:
    """Answers from documents the caller may read, with the passages it used."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _answer(asst.ask(session, who, body.question, body.cabinet_id))


@router.get("/assistant/settings")
def get_settings(ctx: Ctx, asst: Asst, who: User_) -> AssistantSettingsOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _settings(asst.settings(session))


@router.put("/assistant/settings")
def set_settings(
    body: AssistantSetSettingsRequest, ctx: Ctx, asst: Asst, who: User_
) -> AssistantSettingsOut:
    """Needs the integrations.manage right. Generated answers are off until this turns them on."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return _settings(
            asst.set_settings(session, who, body.generation_enabled, body.excluded_cabinet_ids)
        )


@router.get("/assistant/history")
def history(ctx: Ctx, asst: Asst, who: User_) -> list[AssistantHistoryOut]:
    """The caller's own recent questions. Answers are not kept here: access may have changed."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            AssistantHistoryOut(
                id=row.id, question=row.question, mode=row.mode, created_at=row.created_at
            )
            for row in asst.history(session, who)
        ]
