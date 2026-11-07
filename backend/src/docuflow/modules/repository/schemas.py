"""Request and response shapes for the repository API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from docuflow.modules.identity.schemas import Page


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CabinetOut(Orm):
    id: uuid.UUID
    name: str
    description: str
    status: str
    acl_anchor_id: uuid.UUID
    row_version: int
    created_at: datetime


class FolderOut(Orm):
    id: uuid.UUID
    cabinet_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    status: str
    depth: int
    acl_anchor_id: uuid.UUID
    row_version: int
    created_at: datetime


class DocumentOut(Orm):
    id: uuid.UUID
    cabinet_id: uuid.UUID
    folder_id: uuid.UUID | None
    title: str
    status: str
    acl_anchor_id: uuid.UUID
    size_bytes: int
    row_version: int
    created_at: datetime


class StatsOut(BaseModel):
    subfolders: int
    documents: int
    subtree_documents: int
    storage_bytes: int
    recent: list[DocumentOut]


class CabinetDetail(BaseModel):
    cabinet: CabinetOut
    stats: StatsOut
    permissions: list[str]


class CrumbOut(BaseModel):
    id: uuid.UUID | None
    name: str
    kind: Literal["cabinet", "folder"]


class FolderDetail(BaseModel):
    folder: FolderOut
    breadcrumb: list[CrumbOut]
    stats: StatsOut
    permissions: list[str]


class ChildrenOut(BaseModel):
    folders: list[FolderOut]
    documents: Page[DocumentOut]


class CreateCabinetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class UpdateCabinetRequest(BaseModel):
    row_version: int
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "archived"] | None = None


class CreateFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class UpdateFolderRequest(BaseModel):
    row_version: int
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["active", "archived"] | None = None


class DestinationModel(BaseModel):
    cabinet_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.cabinet_id is None) == (self.folder_id is None):
            raise ValueError("Choose either a cabinet or a folder.")
        return self


class MoveRequest(BaseModel):
    destination: DestinationModel
    row_version: int


class CopyRequest(BaseModel):
    destination: DestinationModel


class CopyResult(BaseModel):
    folder: FolderOut
    copied: int
    skipped: int


class AnchorOut(BaseModel):
    anchor_id: uuid.UUID
