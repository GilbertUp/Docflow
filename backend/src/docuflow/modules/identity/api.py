"""Identity HTTP API under /api/v1. Routes stay thin: they authenticate, check, and delegate."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select

from docuflow.modules.identity import queries, security
from docuflow.modules.identity.admin import AclEntryInput, IdentityAdmin
from docuflow.modules.identity.auth import API_KEY_PREFIX, AuthService, Principal
from docuflow.modules.identity.models import (
    AclAnchor,
    Group,
    Permission,
    Role,
    User,
    UserSession,
)
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.identity.schemas import (
    AclEntryModel,
    AclResponse,
    ApiKeyCreated,
    ApiKeyOut,
    ChangePasswordRequest,
    CreateApiKeyRequest,
    CreateDepartmentRequest,
    CreateGroupRequest,
    CreateRoleRequest,
    CreateUserRequest,
    DepartmentOut,
    GroupOut,
    LoginRequest,
    LoginResponse,
    MeResponse,
    MfaCodeRequest,
    MfaSetupResponse,
    Page,
    PermissionOut,
    RecoveryCodesResponse,
    ReplaceAclRequest,
    ResetPasswordRequest,
    RoleHolderRequest,
    RoleHoldersOut,
    RoleOut,
    SessionOut,
    SetPermissionsRequest,
    UpdateUserRequest,
    UserOut,
)
from docuflow.platform.audit import AuditActor
from docuflow.platform.bootstrap import Services
from docuflow.platform.errors import Forbidden, NotFound, Unauthenticated

COOKIE = "df_session"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MUST_CHANGE_CODE = "password_change_required"


@dataclass(frozen=True)
class ApiContext:
    services: Services
    auth: AuthService
    policy: PolicyService
    admin: IdentityAdmin


def build_context(services: Services, limiter: security.RateLimiter) -> ApiContext:
    policy = PolicyService()
    auth = AuthService(services.db, services.audit, services.keys, limiter)
    return ApiContext(services, auth, policy, IdentityAdmin(services.audit, policy, auth))


def get_ctx(request: Request) -> ApiContext:
    ctx: ApiContext = request.app.state.api
    return ctx


Ctx = Annotated[ApiContext, Depends(get_ctx)]


def actor_of(request: Request) -> AuditActor:
    return AuditActor(
        ip=request.client.host if request.client else None,
        device=request.headers.get("user-agent"),
    )


def _authenticate(request: Request, ctx: ApiContext, *, allow_password_change: bool) -> Principal:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        principal = ctx.auth.authenticate_api_key(header[7:].strip())
    else:
        token = request.cookies.get(COOKIE)
        if not token:
            raise Unauthenticated("Please sign in.")
        principal = ctx.auth.authenticate_session(token)
        request.state.principal = principal
        if request.method not in SAFE_METHODS:
            sent = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(sent, security.csrf_token(token)):
                raise Forbidden("Missing or invalid CSRF token.", code="csrf")
    request.state.principal = principal
    if principal.must_change_password and not allow_password_change:
        raise Forbidden("You must change your password first.", code=MUST_CHANGE_CODE)
    return principal


def current_user(request: Request, ctx: Ctx) -> Principal:
    return _authenticate(request, ctx, allow_password_change=False)


def current_user_any_state(request: Request, ctx: Ctx) -> Principal:
    return _authenticate(request, ctx, allow_password_change=True)


User_ = Annotated[Principal, Depends(current_user)]
UserAnyState = Annotated[Principal, Depends(current_user_any_state)]
Limit = Annotated[int, Query(ge=1, le=queries.MAX_LIMIT)]
Offset = Annotated[int, Query(ge=0)]


def set_cookie(response: Response, ctx: ApiContext, token: str, max_age: int) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=ctx.services.settings.environment == "production",
        path="/",
    )


router = APIRouter(prefix="/api/v1")
auth_router = APIRouter(prefix="/auth", tags=["auth"])
admin_router = APIRouter(tags=["identity admin"])


@auth_router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, ctx: Ctx) -> LoginResponse:
    issued = ctx.auth.login(body.organization, body.email, body.password, actor_of(request))
    max_age = 300 if issued.state == "mfa_pending" else 12 * 3600
    set_cookie(response, ctx, issued.token, max_age)
    return LoginResponse(
        state=issued.state,
        must_change_password=issued.must_change_password,
        csrf_token=security.csrf_token(issued.token) if issued.state == "active" else None,
    )


@auth_router.post("/mfa")
def complete_mfa(
    body: MfaCodeRequest, request: Request, response: Response, ctx: Ctx
) -> LoginResponse:
    pending = request.cookies.get(COOKIE)
    if not pending:
        raise Unauthenticated("Please sign in again.")
    issued = ctx.auth.complete_mfa(pending, body.code, actor_of(request))
    set_cookie(response, ctx, issued.token, 12 * 3600)
    return LoginResponse(
        state="active",
        must_change_password=issued.must_change_password,
        csrf_token=security.csrf_token(issued.token),
    )


@auth_router.post("/logout", status_code=204)
def logout(response: Response, ctx: Ctx, who: UserAnyState) -> None:
    if who.session_id is not None:
        ctx.auth.logout(who)
    response.delete_cookie(COOKIE, path="/")


@auth_router.get("/me")
def me(request: Request, ctx: Ctx, who: UserAnyState) -> MeResponse:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        user = session.get(User, who.user_id)
        assert user is not None
        permissions = sorted(ctx.policy.system_permissions(session, who.tenant_id, who.user_id))
        out = UserOut.model_validate(user)
    token = request.cookies.get(COOKIE)
    return MeResponse(
        user=out,
        system_permissions=permissions,
        csrf_token=security.csrf_token(token) if token and who.kind == "session" else None,
        auth_method="session" if who.kind == "session" else "api_key",
    )


@auth_router.post("/password", status_code=204)
def change_password(body: ChangePasswordRequest, ctx: Ctx, who: UserAnyState) -> None:
    ctx.auth.change_password(who, body.current_password, body.new_password)


@auth_router.post("/mfa/setup")
def mfa_setup(ctx: Ctx, who: User_) -> MfaSetupResponse:
    setup = ctx.auth.begin_mfa_setup(who)
    return MfaSetupResponse(secret=setup.secret, uri=setup.uri)


@auth_router.post("/mfa/confirm")
def mfa_confirm(body: MfaCodeRequest, ctx: Ctx, who: User_) -> RecoveryCodesResponse:
    return RecoveryCodesResponse(recovery_codes=ctx.auth.confirm_mfa(who, body.code))


@auth_router.get("/sessions")
def my_sessions(ctx: Ctx, who: User_) -> list[SessionOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        return [
            SessionOut(
                id=row.id,
                device=row.device,
                ip=row.ip,
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
                expires_at=row.expires_at,
                current=row.id == who.session_id,
            )
            for row in ctx.admin.list_sessions(session, who.user_id)
        ]


@auth_router.delete("/sessions/{session_id}", status_code=204)
def revoke_my_session(session_id: uuid.UUID, ctx: Ctx, who: User_) -> None:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        owned = session.scalar(
            select(UserSession.id).where(
                UserSession.id == session_id, UserSession.user_id == who.user_id
            )
        )
        if owned is None:
            raise NotFound("Session not found.")
        ctx.auth.revoke_session(session, who, session_id, who.user_id)


def _require_any(ctx: ApiContext, session: Any, who: Principal, permissions: list[str]) -> None:
    ctx.admin.require_any_system(session, who, permissions)


@admin_router.get("/permissions")
def permissions_catalogue(ctx: Ctx, who: User_) -> list[PermissionOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        _require_any(ctx, session, who, ["users.manage", "groups.manage", "security.admin"])
        rows = session.scalars(select(Permission).order_by(Permission.kind, Permission.code))
        return [PermissionOut.model_validate(row) for row in rows]


@admin_router.get("/users")
def list_users(
    ctx: Ctx,
    who: User_,
    limit: Limit = 50,
    offset: Offset = 0,
    search: Annotated[str | None, Query(max_length=100)] = None,
    kind: Annotated[str, Query(pattern="^(person|service)$")] = "person",
) -> Page[UserOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        _require_any(ctx, session, who, ["users.manage", "groups.manage"])
        rows, total = queries.list_users(session, limit, offset, search, kind)
        items = [UserOut.model_validate(row) for row in rows]
    return Page[UserOut](items=items, total=total, limit=limit, offset=offset)


@admin_router.post("/users", status_code=201)
def create_user(body: CreateUserRequest, ctx: Ctx, who: User_) -> UserOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "users.manage")
        user = ctx.admin.create_user(
            session, who, body.email, body.display_name, body.password, body.department_id
        )
        return UserOut.model_validate(user)


@admin_router.patch("/users/{user_id}")
def update_user(user_id: uuid.UUID, body: UpdateUserRequest, ctx: Ctx, who: User_) -> UserOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "users.manage")
        user = ctx.admin.update_user(
            session,
            who,
            user_id,
            row_version=body.row_version,
            display_name=body.display_name,
            status=body.status,
            department_id=body.department_id,
            clear_department=body.clear_department,
        )
        return UserOut.model_validate(user)


@admin_router.post("/users/{user_id}/password-reset", status_code=204)
def reset_password(user_id: uuid.UUID, body: ResetPasswordRequest, ctx: Ctx, who: User_) -> None:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "users.manage")
        ctx.admin.reset_password(session, who, user_id, body.password)


@admin_router.get("/users/{user_id}/effective-permissions")
def effective_permissions(
    user_id: uuid.UUID, anchor_id: uuid.UUID, ctx: Ctx, who: User_
) -> dict[str, dict[str, Any]]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "users.manage")
        return ctx.admin.effective_permissions(session, who.tenant_id, user_id, anchor_id)


@admin_router.get("/departments")
def list_departments(
    ctx: Ctx, who: User_, limit: Limit = 100, offset: Offset = 0
) -> Page[DepartmentOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        _require_any(ctx, session, who, ["departments.manage", "users.manage"])
        rows, total = queries.list_departments(session, limit, offset)
        items = [DepartmentOut.model_validate(row) for row in rows]
    return Page[DepartmentOut](items=items, total=total, limit=limit, offset=offset)


@admin_router.post("/departments", status_code=201)
def create_department(body: CreateDepartmentRequest, ctx: Ctx, who: User_) -> DepartmentOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "departments.manage")
        return DepartmentOut.model_validate(
            ctx.admin.create_department(session, who, body.name, body.parent_id)
        )


@admin_router.get("/groups")
def list_groups(ctx: Ctx, who: User_, limit: Limit = 100, offset: Offset = 0) -> Page[GroupOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        _require_any(ctx, session, who, ["groups.manage", "users.manage"])
        rows, total = queries.list_groups(session, limit, offset)
        items = [GroupOut.model_validate(row) for row in rows]
    return Page[GroupOut](items=items, total=total, limit=limit, offset=offset)


@admin_router.post("/groups", status_code=201)
def create_group(body: CreateGroupRequest, ctx: Ctx, who: User_) -> GroupOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "groups.manage")
        return GroupOut.model_validate(
            ctx.admin.create_group(session, who, body.name, body.description)
        )


@admin_router.get("/groups/{group_id}/members")
def group_members(group_id: uuid.UUID, ctx: Ctx, who: User_) -> list[UserOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        _require_any(ctx, session, who, ["groups.manage", "users.manage"])
        if session.get(Group, group_id) is None:
            raise NotFound("Group not found.")
        return [UserOut.model_validate(row) for row in queries.group_members(session, group_id)]


@admin_router.put("/groups/{group_id}/members/{user_id}", status_code=204)
def add_member(group_id: uuid.UUID, user_id: uuid.UUID, ctx: Ctx, who: User_) -> None:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "groups.manage")
        ctx.admin.add_member(session, who, group_id, user_id)


@admin_router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
def remove_member(group_id: uuid.UUID, user_id: uuid.UUID, ctx: Ctx, who: User_) -> None:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "groups.manage")
        ctx.admin.remove_member(session, who, group_id, user_id)


@admin_router.get("/roles")
def list_roles(ctx: Ctx, who: User_, limit: Limit = 100, offset: Offset = 0) -> Page[RoleOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        _require_any(ctx, session, who, ["security.admin", "users.manage", "groups.manage"])
        rows, total = queries.list_roles(session, limit, offset)
        items = [
            RoleOut(id=role.id, name=role.name, description=role.description, permissions=granted)
            for role, granted in rows
        ]
    return Page[RoleOut](items=items, total=total, limit=limit, offset=offset)


@admin_router.post("/roles", status_code=201)
def create_role(body: CreateRoleRequest, ctx: Ctx, who: User_) -> RoleOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "security.admin")
        role = ctx.admin.create_role(session, who, body.name, body.description, body.permissions)
        return RoleOut(
            id=role.id,
            name=role.name,
            description=role.description,
            permissions=sorted(set(body.permissions)),
        )


@admin_router.put("/roles/{role_id}/permissions", status_code=204)
def set_role_permissions(
    role_id: uuid.UUID, body: SetPermissionsRequest, ctx: Ctx, who: User_
) -> None:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "security.admin")
        ctx.admin.set_role_permissions(session, who, role_id, body.permissions)


@admin_router.get("/roles/{role_id}/assignments")
def role_assignments(role_id: uuid.UUID, ctx: Ctx, who: User_) -> RoleHoldersOut:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        _require_any(ctx, session, who, ["security.admin", "users.manage", "groups.manage"])
        if session.get(Role, role_id) is None:
            raise NotFound("Role not found.")
        users, groups = queries.role_holders(session, role_id)
        return RoleHoldersOut(
            users=[UserOut.model_validate(item) for item in users],
            groups=[GroupOut.model_validate(item) for item in groups],
        )


def _holder_permission(body: RoleHolderRequest) -> str:
    return "groups.manage" if body.group_id else "users.manage"


@admin_router.put("/roles/{role_id}/assignments", status_code=204)
def assign_role(role_id: uuid.UUID, body: RoleHolderRequest, ctx: Ctx, who: User_) -> None:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, _holder_permission(body))
        ctx.admin.assign_role(session, who, role_id, body.user_id, body.group_id)


@admin_router.post("/roles/{role_id}/assignments/remove", status_code=204)
def unassign_role(role_id: uuid.UUID, body: RoleHolderRequest, ctx: Ctx, who: User_) -> None:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, _holder_permission(body))
        ctx.admin.unassign_role(session, who, role_id, body.user_id, body.group_id)


@admin_router.get("/api-keys")
def list_api_keys(ctx: Ctx, who: User_, limit: Limit = 50, offset: Offset = 0) -> Page[ApiKeyOut]:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "integrations.manage")
        rows, total = queries.list_api_keys(session, limit, offset)
        items = [ApiKeyOut.model_validate(row) for row in rows]
    return Page[ApiKeyOut](items=items, total=total, limit=limit, offset=offset)


@admin_router.post("/api-keys", status_code=201)
def create_api_key(body: CreateApiKeyRequest, ctx: Ctx, who: User_) -> ApiKeyCreated:
    """The token is shown once. Only its hash is stored."""
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "integrations.manage")
        key, token = ctx.admin.create_api_key(session, who, body.name, body.expires_days)
        assert token.startswith(API_KEY_PREFIX)
        return ApiKeyCreated(**ApiKeyOut.model_validate(key).model_dump(), token=token)


@admin_router.delete("/api-keys/{key_id}", status_code=204)
def revoke_api_key(key_id: uuid.UUID, ctx: Ctx, who: User_) -> None:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        ctx.admin.require_system(session, who, "integrations.manage")
        ctx.admin.revoke_api_key(session, who, key_id)


def _anchor(session: Any, anchor_id: uuid.UUID) -> AclAnchor:
    anchor = session.get(AclAnchor, anchor_id)
    if anchor is None:
        raise NotFound("Not found.")
    return anchor  # type: ignore[no-any-return]


@admin_router.get("/acl/anchors/{anchor_id}")
def get_acl(anchor_id: uuid.UUID, ctx: Ctx, who: User_) -> AclResponse:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        anchor = _anchor(session, anchor_id)
        entries = ctx.admin.get_acl(session, who, anchor)
        return AclResponse(
            anchor_id=anchor.id,
            entries=[
                AclEntryModel(
                    principal_type=item.principal_type,
                    principal_id=item.principal_id,
                    permission=item.permission,
                    effect=item.effect,
                )
                for item in entries
            ],
        )


@admin_router.put("/acl/anchors/{anchor_id}", status_code=204)
def replace_acl(anchor_id: uuid.UUID, body: ReplaceAclRequest, ctx: Ctx, who: User_) -> None:
    with ctx.services.db.tenant_session(who.tenant_id) as session:
        anchor = _anchor(session, anchor_id)
        ctx.admin.replace_acl(
            session,
            who,
            anchor,
            [
                AclEntryInput(item.principal_type, item.principal_id, item.permission, item.effect)
                for item in body.entries
            ],
        )


router.include_router(auth_router)
router.include_router(admin_router)

# Routes that work without a session. Everything else must reject anonymous callers, and a test
# walks every route to prove it.
PUBLIC_ROUTES = frozenset({("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/mfa")})
