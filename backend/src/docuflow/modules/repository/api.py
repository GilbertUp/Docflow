"""Repository HTTP API: cabinets, folders and documents under /api/v1."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from docuflow.modules.identity.api import Ctx, Limit, Offset, User_
from docuflow.modules.identity.schemas import Page
from docuflow.modules.repository.schemas import (
    AnchorOut,
    CabinetDetail,
    CabinetOut,
    ChildrenOut,
    CopyRequest,
    CopyResult,
    CreateCabinetRequest,
    CreateFolderRequest,
    CrumbOut,
    DestinationModel,
    DocumentOut,
    FolderDetail,
    FolderOut,
    MoveRequest,
    StatsOut,
    UpdateCabinetRequest,
    UpdateFolderRequest,
)
from docuflow.modules.repository.service import Destination, RepositoryService, Stats

router = APIRouter(prefix="/api/v1", tags=["repository"])


def get_repo(request: Request) -> RepositoryService:
    repo: RepositoryService = request.app.state.repo
    return repo


Repo = Annotated[RepositoryService, Depends(get_repo)]
IncludeArchived = Annotated[bool, Query()]


def _stats(stats: Stats) -> StatsOut:
    return StatsOut(
        subfolders=stats.subfolders,
        documents=stats.documents,
        subtree_documents=stats.subtree_documents,
        storage_bytes=stats.storage_bytes,
        recent=[DocumentOut.model_validate(item) for item in stats.recent],
    )


def _destination(model: DestinationModel) -> Destination:
    return Destination(cabinet_id=model.cabinet_id, folder_id=model.folder_id)


@router.get("/cabinets")
def list_cabinets(
    ctx: Ctx,
    repo: Repo,
    who: User_,
    limit: Limit = 50,
    offset: Offset = 0,
    include_archived: IncludeArchived = False,
) -> Page[CabinetOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        rows, total = repo.list_cabinets(
            session, who, include_archived=include_archived, limit=limit, offset=offset
        )
        items = [CabinetOut.model_validate(row) for row in rows]
    return Page[CabinetOut](items=items, total=total, limit=limit, offset=offset)


@router.post("/cabinets", status_code=201)
def create_cabinet(body: CreateCabinetRequest, ctx: Ctx, repo: Repo, who: User_) -> CabinetOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return CabinetOut.model_validate(
            repo.create_cabinet(session, who, body.name, body.description)
        )


@router.get("/cabinets/{cabinet_id}")
def get_cabinet(cabinet_id: uuid.UUID, ctx: Ctx, repo: Repo, who: User_) -> CabinetDetail:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        cabinet = repo.get_cabinet(session, who, cabinet_id)
        stats = repo.stats(session, who, Destination(cabinet_id=cabinet_id))
        return CabinetDetail(cabinet=CabinetOut.model_validate(cabinet), stats=_stats(stats))


@router.patch("/cabinets/{cabinet_id}")
def update_cabinet(
    cabinet_id: uuid.UUID, body: UpdateCabinetRequest, ctx: Ctx, repo: Repo, who: User_
) -> CabinetOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return CabinetOut.model_validate(
            repo.update_cabinet(
                session,
                who,
                cabinet_id,
                row_version=body.row_version,
                name=body.name,
                description=body.description,
                status=body.status,
            )
        )


@router.delete("/cabinets/{cabinet_id}", status_code=204)
def delete_cabinet(cabinet_id: uuid.UUID, ctx: Ctx, repo: Repo, who: User_) -> None:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        repo.delete_cabinet(session, who, cabinet_id)


def _children(
    ctx: Ctx,
    repo: RepositoryService,
    who: User_,
    container: Destination,
    include_archived: bool,
    limit: int,
    offset: int,
) -> ChildrenOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        result = repo.children(
            session, who, container, include_archived=include_archived, limit=limit, offset=offset
        )
        return ChildrenOut(
            folders=[FolderOut.model_validate(item) for item in result.folders],
            documents=Page[DocumentOut](
                items=[DocumentOut.model_validate(item) for item in result.documents],
                total=result.total_documents,
                limit=limit,
                offset=offset,
            ),
        )


@router.get("/cabinets/{cabinet_id}/children")
def cabinet_children(
    cabinet_id: uuid.UUID,
    ctx: Ctx,
    repo: Repo,
    who: User_,
    limit: Limit = 50,
    offset: Offset = 0,
    include_archived: IncludeArchived = False,
) -> ChildrenOut:
    return _children(
        ctx, repo, who, Destination(cabinet_id=cabinet_id), include_archived, limit, offset
    )


@router.post("/cabinets/{cabinet_id}/folders", status_code=201)
def create_root_folder(
    cabinet_id: uuid.UUID, body: CreateFolderRequest, ctx: Ctx, repo: Repo, who: User_
) -> FolderOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        folder = repo.create_folder(session, who, Destination(cabinet_id=cabinet_id), body.name)
        return FolderOut.model_validate(folder)


@router.get("/folders/{folder_id}")
def get_folder(folder_id: uuid.UUID, ctx: Ctx, repo: Repo, who: User_) -> FolderDetail:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        folder, crumbs = repo.get_folder(session, who, folder_id)
        stats = repo.stats(session, who, Destination(folder_id=folder_id))
        return FolderDetail(
            folder=FolderOut.model_validate(folder),
            breadcrumb=[CrumbOut(id=c.id, name=c.name, kind=c.kind) for c in crumbs],
            stats=_stats(stats),
        )


@router.get("/folders/{folder_id}/children")
def folder_children(
    folder_id: uuid.UUID,
    ctx: Ctx,
    repo: Repo,
    who: User_,
    limit: Limit = 50,
    offset: Offset = 0,
    include_archived: IncludeArchived = False,
) -> ChildrenOut:
    return _children(
        ctx, repo, who, Destination(folder_id=folder_id), include_archived, limit, offset
    )


@router.post("/folders/{folder_id}/folders", status_code=201)
def create_subfolder(
    folder_id: uuid.UUID, body: CreateFolderRequest, ctx: Ctx, repo: Repo, who: User_
) -> FolderOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        folder = repo.create_folder(session, who, Destination(folder_id=folder_id), body.name)
        return FolderOut.model_validate(folder)


@router.patch("/folders/{folder_id}")
def update_folder(
    folder_id: uuid.UUID, body: UpdateFolderRequest, ctx: Ctx, repo: Repo, who: User_
) -> FolderOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return FolderOut.model_validate(
            repo.update_folder(
                session,
                who,
                folder_id,
                row_version=body.row_version,
                name=body.name,
                status=body.status,
            )
        )


@router.post("/folders/{folder_id}/move")
def move_folder(
    folder_id: uuid.UUID, body: MoveRequest, ctx: Ctx, repo: Repo, who: User_
) -> FolderOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return FolderOut.model_validate(
            repo.move_folder(
                session, who, folder_id, _destination(body.destination), body.row_version
            )
        )


@router.post("/folders/{folder_id}/copy", status_code=201)
def copy_folder(
    folder_id: uuid.UUID, body: CopyRequest, ctx: Ctx, repo: Repo, who: User_
) -> CopyResult:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        folder, copied, skipped = repo.copy_folder(
            session, who, folder_id, _destination(body.destination)
        )
        return CopyResult(folder=FolderOut.model_validate(folder), copied=copied, skipped=skipped)


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(folder_id: uuid.UUID, ctx: Ctx, repo: Repo, who: User_) -> None:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        repo.delete_folder(session, who, folder_id)


@router.get("/documents/{document_id}")
def get_document(document_id: uuid.UUID, ctx: Ctx, repo: Repo, who: User_) -> DocumentOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return DocumentOut.model_validate(repo.get_document(session, who, document_id))


@router.post("/documents/{document_id}/move")
def move_document(
    document_id: uuid.UUID, body: MoveRequest, ctx: Ctx, repo: Repo, who: User_
) -> DocumentOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return DocumentOut.model_validate(
            repo.move_document(
                session, who, document_id, _destination(body.destination), body.row_version
            )
        )


@router.post("/documents/{document_id}/acl-anchor")
def document_acl_anchor(document_id: uuid.UUID, ctx: Ctx, repo: Repo, who: User_) -> AnchorOut:
    """Gives the document its own permissions. Edit them through /acl/anchors/{anchor_id}."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return AnchorOut(anchor_id=repo.ensure_document_anchor(session, who, document_id))
