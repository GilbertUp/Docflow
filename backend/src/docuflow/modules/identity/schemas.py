"""Request and response shapes for the identity API. Secrets never appear in responses."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class LoginRequest(BaseModel):
    organization: str = Field(min_length=1, max_length=63)
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    state: Literal["active", "mfa_pending"]
    must_change_password: bool
    csrf_token: str | None = None


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class MfaSetupResponse(BaseModel):
    secret: str
    uri: str


class RecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]


class UserOut(Orm):
    id: uuid.UUID
    email: str
    display_name: str
    kind: str
    status: str
    department_id: uuid.UUID | None
    mfa_enabled: bool
    must_change_password: bool
    last_login_at: datetime | None
    row_version: int
    created_at: datetime


class MeResponse(BaseModel):
    user: UserOut
    system_permissions: list[str]
    csrf_token: str | None
    auth_method: Literal["session", "api_key"]


class SessionOut(BaseModel):
    id: uuid.UUID
    device: str | None
    ip: str | None
    created_at: datetime
    last_seen_at: datetime | None
    expires_at: datetime
    current: bool


class CreateUserRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=256)
    department_id: uuid.UUID | None = None


class UpdateUserRequest(BaseModel):
    row_version: int
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["active", "disabled"] | None = None
    department_id: uuid.UUID | None = None
    clear_department: bool = False


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class GroupOut(Orm):
    id: uuid.UUID
    name: str
    description: str


class CreateDepartmentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None


class DepartmentOut(Orm):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None


class CreateRoleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    permissions: list[str] = Field(default_factory=list, max_length=100)


class SetPermissionsRequest(BaseModel):
    permissions: list[str] = Field(max_length=100)


class RoleOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    permissions: list[str]


class RoleHolderRequest(BaseModel):
    user_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None


class PermissionOut(Orm):
    code: str
    kind: str
    description: str


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    expires_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyOut(Orm):
    id: uuid.UUID
    name: str
    prefix: str
    service_user_id: uuid.UUID
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyOut):
    token: str


class AclEntryModel(BaseModel):
    principal_type: Literal["user", "group", "role", "department"]
    principal_id: uuid.UUID
    permission: str = Field(max_length=60)
    effect: Literal["allow", "deny"]


class AclResponse(BaseModel):
    anchor_id: uuid.UUID
    entries: list[AclEntryModel]


class ReplaceAclRequest(BaseModel):
    entries: list[AclEntryModel] = Field(max_length=500)
