"""Search HTTP API."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from docuflow.modules.identity.api import Ctx, User_
from docuflow.modules.search.schemas import HitOut, SearchOut
from docuflow.modules.search.service import SearchService, clean_query

router = APIRouter(prefix="/api/v1", tags=["search"])


def get_search(request: Request) -> SearchService:
    service: SearchService = request.app.state.search
    return service


Search = Annotated[SearchService, Depends(get_search)]


@router.get("/search")
def search_documents(
    ctx: Ctx,
    service: Search,
    who: User_,
    q: Annotated[str, Query(max_length=400)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    type_id: uuid.UUID | None = None,
    cabinet_id: uuid.UUID | None = None,
) -> SearchOut:
    """Documents whose title, details or text match, limited to what the caller may read."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        found = service.search(
            session, who, q, limit=limit, offset=offset, type_id=type_id, cabinet_id=cabinet_id
        )
        return SearchOut(
            query=clean_query(q),
            total=found.total,
            truncated=found.truncated,
            hits=[
                HitOut(
                    id=hit.document.id,
                    title=hit.document.title,
                    cabinet_id=hit.document.cabinet_id,
                    folder_id=hit.document.folder_id,
                    document_type_id=hit.document.document_type_id,
                    type_name=hit.type_name,
                    size_bytes=hit.document.size_bytes,
                    created_at=hit.document.created_at,
                    matched_in=hit.matched_in,
                    score=hit.rank,
                    snippet=hit.snippet,
                )
                for hit in found.hits
            ],
        )
