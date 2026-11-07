"""Authentication: password login, sessions, TOTP MFA and API keys (ADR 0005)."""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import pyotp
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from docuflow.modules.identity import security
from docuflow.modules.identity.models import (
    ApiKey,
    MfaCredential,
    MfaRecoveryCode,
    User,
    UserSession,
)
from docuflow.modules.identity.security import RateLimiter
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.crypto import KeyProvider
from docuflow.platform.db import Database, utcnow
from docuflow.platform.errors import Conflict, DomainError, RateLimited, Unauthenticated
from docuflow.platform.models import Tenant

API_KEY_PREFIX = "dfk_"
SESSION_LIFETIME = timedelta(hours=12)
MFA_PENDING_LIFETIME = timedelta(minutes=5)
MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)
IP_ATTEMPTS_PER_MINUTE = 30
RECOVERY_CODE_COUNT = 10
TOTP_STEP_SECONDS = 30
BAD_LOGIN = "Incorrect organization, email or password."


@dataclass(frozen=True)
class Principal:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    kind: str  # "session" or "api_key"
    session_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None
    must_change_password: bool = False


@dataclass(frozen=True)
class IssuedSession:
    token: str
    state: str
    must_change_password: bool


@dataclass(frozen=True)
class MfaSetup:
    secret: str
    uri: str


def _hash_recovery(tenant_id: uuid.UUID, user_id: uuid.UUID, code: str) -> str:
    normalized = code.replace("-", "").strip().lower()
    return hashlib.sha256(f"{tenant_id}:{user_id}:{normalized}".encode()).hexdigest()


class AuthService:
    def __init__(
        self, db: Database, audit: AuditLog, keys: KeyProvider, limiter: RateLimiter
    ) -> None:
        self._db = db
        self._audit = audit
        self._keys = keys
        self._limiter = limiter

    def _tenant_id(self, slug: str) -> uuid.UUID | None:
        with self._db.platform_session() as session:
            return session.scalar(
                select(Tenant.id).where(Tenant.slug == slug.lower(), Tenant.status == "active")
            )

    def login(
        self, slug: str, email: str, password: str, actor: AuditActor | None = None
    ) -> IssuedSession:
        actor = actor or AuditActor()
        if not self._limiter.allow(f"login:{actor.ip}", IP_ATTEMPTS_PER_MINUTE, 60):
            raise RateLimited("Too many attempts. Please wait a minute.")
        tenant_id = self._tenant_id(slug)
        if tenant_id is None:
            security.verify_password(None, password)
            raise Unauthenticated(BAD_LOGIN)
        # Failures must commit their counters and audit, so the error is raised outside the block.
        issued: IssuedSession | None = None
        with self._db.tenant_session(tenant_id) as session:
            user = session.scalar(
                select(User).where(User.email == email.strip().lower()).with_for_update()
            )
            issued = self._attempt(session, tenant_id, user, email, password, actor)
        if issued is None:
            raise Unauthenticated(BAD_LOGIN)
        return issued

    def _attempt(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        user: User | None,
        email: str,
        password: str,
        actor: AuditActor,
    ) -> IssuedSession | None:
        now = utcnow()
        locked = user is not None and user.locked_until is not None and user.locked_until > now
        usable = user is not None and user.status == "active" and user.kind == "person"
        verified = security.verify_password(user.password_hash if user else None, password)
        if user is None or not usable or locked or not verified:
            reason = "locked" if locked else "bad_credentials"
            if user is not None and usable and not locked:
                user.failed_logins += 1
                if user.failed_logins >= MAX_FAILED_LOGINS:
                    user.locked_until = now + LOCKOUT
                    user.failed_logins = 0
                    reason = "locked"
                    self._audit.record(
                        session,
                        tenant_id,
                        "auth.account_locked",
                        actor=actor,
                        object_type="user",
                        object_id=str(user.id),
                    )
            self._audit.record(
                session,
                tenant_id,
                "auth.login",
                actor=actor,
                object_type="user",
                object_id=str(user.id) if user else None,
                result="failure",
                details={"reason": reason, "email_hash": security.hash_token(email.lower())},
            )
            return None
        assert user.password_hash is not None
        if security.needs_rehash(user.password_hash):
            user.password_hash = security.hash_password(password)
        user.failed_logins = 0
        user.locked_until = None
        state = "mfa_pending" if user.mfa_enabled else "active"
        token = self._new_session(session, user, state, actor)
        if state == "active":
            user.last_login_at = now
        self._audit.record(
            session,
            tenant_id,
            "auth.login",
            actor=AuditActor(user.id, "user", actor.ip, actor.device),
            object_type="user",
            object_id=str(user.id),
            details={"state": state},
        )
        return IssuedSession(token, state, user.must_change_password)

    def create_session_for(
        self, session: Session, user: User, actor: AuditActor, method: str
    ) -> IssuedSession:
        """Used after an external login such as OIDC, where the IdP performed authentication."""
        token = self._new_session(session, user, "active", actor)
        user.last_login_at = utcnow()
        self._audit.record(
            session,
            user.tenant_id,
            "auth.login",
            actor=AuditActor(user.id, "user", actor.ip, actor.device),
            object_type="user",
            object_id=str(user.id),
            details={"method": method},
        )
        return IssuedSession(token, "active", False)

    def _new_session(self, session: Session, user: User, state: str, actor: AuditActor) -> str:
        token = security.make_token(user.tenant_id)
        lifetime = MFA_PENDING_LIFETIME if state == "mfa_pending" else SESSION_LIFETIME
        session.add(
            UserSession(
                tenant_id=user.tenant_id,
                user_id=user.id,
                token_hash=security.hash_token(token),
                state=state,
                device=(actor.device or "")[:255] or None,
                ip=actor.ip,
                expires_at=utcnow() + lifetime,
            )
        )
        return token

    def _load_session(self, session: Session, token: str) -> UserSession | None:
        row = session.scalar(
            select(UserSession).where(UserSession.token_hash == security.hash_token(token))
        )
        if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
            return None
        return row

    def authenticate_session(self, token: str) -> Principal:
        tenant_id = security.parse_tenant(token)
        if tenant_id is None:
            raise Unauthenticated("Please sign in.")
        with self._db.tenant_session(tenant_id) as session:
            row = self._load_session(session, token)
            if row is None or row.state != "active":
                raise Unauthenticated("Please sign in.")
            user = session.get(User, row.user_id)
            if user is None or user.status != "active":
                raise Unauthenticated("Please sign in.")
            row.last_seen_at = utcnow()
            return Principal(
                tenant_id,
                user.id,
                "session",
                session_id=row.id,
                must_change_password=user.must_change_password,
            )

    def authenticate_api_key(self, token: str) -> Principal:
        tenant_id = security.parse_tenant(token, API_KEY_PREFIX)
        if tenant_id is None:
            raise Unauthenticated("Invalid API key.")
        with self._db.tenant_session(tenant_id) as session:
            key = session.scalar(
                select(ApiKey).where(ApiKey.key_hash == security.hash_token(token))
            )
            now = utcnow()
            if (
                key is None
                or key.revoked_at is not None
                or (key.expires_at is not None and key.expires_at <= now)
            ):
                raise Unauthenticated("Invalid API key.")
            user = session.get(User, key.service_user_id)
            if user is None or user.status != "active":
                raise Unauthenticated("Invalid API key.")
            key.last_used_at = now
            return Principal(tenant_id, user.id, "api_key", api_key_id=key.id)

    def logout(self, principal: Principal) -> None:
        with self._db.tenant_session(principal.tenant_id) as session:
            self.revoke_session(session, principal, principal.session_id, principal.user_id)

    def revoke_session(
        self,
        session: Session,
        principal: Principal,
        session_id: uuid.UUID | None,
        owner_id: uuid.UUID,
    ) -> None:
        session.execute(
            update(UserSession)
            .where(
                UserSession.id == session_id,
                UserSession.user_id == owner_id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )
        self._audit.record(
            session,
            principal.tenant_id,
            "auth.logout" if session_id == principal.session_id else "auth.session_revoked",
            actor=AuditActor(principal.user_id, "user"),
            object_type="session",
            object_id=str(session_id),
        )

    def revoke_all(
        self, session: Session, user_id: uuid.UUID, keep: uuid.UUID | None = None
    ) -> None:
        statement = (
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        if keep is not None:
            statement = statement.where(UserSession.id != keep)
        session.execute(statement)

    def change_password(self, principal: Principal, current: str, new: str) -> None:
        with self._db.tenant_session(principal.tenant_id) as session:
            user = session.get(User, principal.user_id)
            if user is None or not security.verify_password(user.password_hash, current):
                raise DomainError("Current password is incorrect.", "bad_password")
            security.validate_password(new, user.email)
            user.password_hash = security.hash_password(new)
            user.must_change_password = False
            self.revoke_all(session, user.id, keep=principal.session_id)
            self._audit.record(
                session,
                principal.tenant_id,
                "auth.password_changed",
                actor=AuditActor(user.id, "user"),
                object_type="user",
                object_id=str(user.id),
            )

    def begin_mfa_setup(self, principal: Principal) -> MfaSetup:
        with self._db.tenant_session(principal.tenant_id) as session:
            user = session.get(User, principal.user_id)
            assert user is not None
            if user.mfa_enabled:
                raise Conflict("Multi-factor authentication is already on.")
            existing = session.scalar(select(MfaCredential).where(MfaCredential.user_id == user.id))
            if existing is not None:
                session.delete(existing)
                session.flush()
            secret = pyotp.random_base32()
            session.add(
                MfaCredential(
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    secret_encrypted=self._keys.wrap(user.tenant_id, secret.encode()),
                )
            )
            uri = pyotp.TOTP(secret).provisioning_uri(user.email, issuer_name="DocuFlow")
            return MfaSetup(secret, uri)

    def confirm_mfa(self, principal: Principal, code: str) -> list[str]:
        """Turns MFA on only after a valid code, then returns one-time recovery codes."""
        with self._db.tenant_session(principal.tenant_id) as session:
            credential = session.scalar(
                select(MfaCredential)
                .where(MfaCredential.user_id == principal.user_id)
                .with_for_update()
            )
            if credential is None or not self._check_totp(credential, code):
                raise DomainError("That code is not valid.", "bad_mfa_code")
            credential.confirmed_at = utcnow()
            user = session.get(User, principal.user_id)
            assert user is not None
            user.mfa_enabled = True
            codes = [
                f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODE_COUNT)
            ]
            session.add_all(
                MfaRecoveryCode(
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    code_hash=_hash_recovery(user.tenant_id, user.id, code_value),
                )
                for code_value in codes
            )
            self.revoke_all(session, user.id, keep=principal.session_id)
            self._audit.record(
                session,
                principal.tenant_id,
                "auth.mfa_enabled",
                actor=AuditActor(user.id, "user"),
                object_type="user",
                object_id=str(user.id),
            )
            return codes

    def complete_mfa(self, token: str, code: str, actor: AuditActor | None = None) -> IssuedSession:
        """Exchanges a pending session for a new active one, or accepts a recovery code."""
        actor = actor or AuditActor()
        tenant_id = security.parse_tenant(token)
        if tenant_id is None or not self._limiter.allow(
            f"mfa:{security.hash_token(token)}", 5, 300
        ):
            raise Unauthenticated("Please sign in again.")
        issued: IssuedSession | None = None
        with self._db.tenant_session(tenant_id) as session:
            pending = self._load_session(session, token)
            if pending is None or pending.state != "mfa_pending":
                raise Unauthenticated("Please sign in again.")
            user = session.get(User, pending.user_id)
            credential = session.scalar(
                select(MfaCredential)
                .where(MfaCredential.user_id == pending.user_id)
                .with_for_update()
            )
            if user is None or credential is None:
                raise Unauthenticated("Please sign in again.")
            method = self._verify_factor(session, user, credential, code)
            if method is None:
                self._audit.record(
                    session,
                    tenant_id,
                    "auth.mfa_failed",
                    actor=AuditActor(user.id, "user", actor.ip, actor.device),
                    object_type="user",
                    object_id=str(user.id),
                    result="failure",
                )
            else:
                pending.revoked_at = utcnow()
                new_token = self._new_session(session, user, "active", actor)
                user.last_login_at = utcnow()
                self._audit.record(
                    session,
                    tenant_id,
                    "auth.mfa_verified",
                    actor=AuditActor(user.id, "user", actor.ip, actor.device),
                    object_type="user",
                    object_id=str(user.id),
                    details={"method": method},
                )
                issued = IssuedSession(new_token, "active", user.must_change_password)
        if issued is None:
            raise DomainError("That code is not valid.", "bad_mfa_code")
        return issued

    def _verify_factor(
        self, session: Session, user: User, credential: MfaCredential, code: str
    ) -> str | None:
        if self._check_totp(credential, code):
            return "totp"
        recovery = session.scalar(
            select(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.user_id == user.id,
                MfaRecoveryCode.code_hash == _hash_recovery(user.tenant_id, user.id, code),
                MfaRecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        )
        if recovery is not None:
            recovery.used_at = utcnow()
            return "recovery_code"
        return None

    def _check_totp(self, credential: MfaCredential, code: str, at: datetime | None = None) -> bool:
        """Accepts one step of clock drift, and each step only once to prevent replay."""
        secret = self._keys.unwrap(credential.tenant_id, credential.secret_encrypted).decode()
        totp = pyotp.TOTP(secret)
        now = at.timestamp() if at else time.time()
        current = int(now) // TOTP_STEP_SECONDS
        for step in (current - 1, current, current + 1):
            if step > credential.last_step and secrets.compare_digest(
                totp.at(step * TOTP_STEP_SECONDS), code.strip()
            ):
                credential.last_step = step
                return True
        return False
