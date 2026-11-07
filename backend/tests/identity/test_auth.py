"""F01 authentication: passwords, sessions, lockout, MFA. Real PostgreSQL, real hashing."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import pyotp
import pytest
from sqlalchemy import select, update

from docuflow.modules.identity import security
from docuflow.modules.identity.auth import AuthService, IssuedSession
from docuflow.modules.identity.models import MfaCredential, User, UserSession
from docuflow.platform.audit import AuditActor, AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database, utcnow
from docuflow.platform.errors import Conflict, DomainError, RateLimited, Unauthenticated
from tests.identity.support import (
    STRONG,
    audit_events,
    email,
    enable_mfa,
    new_user,
    tenant_slug,
)

pytestmark = pytest.mark.db
BAD = "Incorrect organization, email or password."
PAST = timedelta(minutes=1)


def login(
    db: Database, auth: AuthService, tenant: uuid.UUID, name: str, password: str = STRONG
) -> IssuedSession:
    return auth.login(tenant_slug(db, tenant), email(name), password)


def test_passwords_use_argon2id() -> None:
    assert security.hash_password(STRONG).startswith("$argon2id$")


@pytest.mark.parametrize(
    "password",
    ["short1", "password1234", "alice-is-my-name-1", "x" * 11],
)
def test_weak_passwords_are_rejected(password: str) -> None:
    with pytest.raises(DomainError) as caught:
        security.validate_password(password, "alice@example.com")
    assert caught.value.code == "weak_password"


def test_strong_password_is_accepted() -> None:
    security.validate_password(STRONG, "alice@example.com")


def test_login_creates_a_session_and_an_audit_event(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    user_id = new_user(db, tenant, "alice")
    issued = login(db, auth, tenant, "alice")
    assert issued.state == "active"
    found = auth.authenticate_session(issued.token)
    assert (found.tenant_id, found.user_id) == (tenant, user_id)
    event = audit_events(db, tenant, "auth.login")[-1]
    assert (event.result, event.actor_id) == ("success", user_id)


def test_only_a_hash_of_the_token_is_stored(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    issued = login(db, auth, tenant, "alice")
    with db.tenant_session(tenant) as session:
        stored = session.scalars(select(UserSession.token_hash)).all()
    assert issued.token not in stored
    assert security.hash_token(issued.token) in stored


def test_wrong_password_and_unknown_user_are_indistinguishable(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    messages = []
    for name, password in (("alice", "wrong-password-1"), ("nobody", STRONG)):
        with pytest.raises(Unauthenticated) as caught:
            login(db, auth, tenant, name, password)
        messages.append(caught.value.message)
    assert messages == [BAD, BAD]


def test_unknown_organization_gives_the_same_message(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    with pytest.raises(Unauthenticated) as caught:
        auth.login("no-such-org", email("alice"), STRONG)
    assert caught.value.message == BAD


def test_failed_login_is_audited_without_the_email_or_password(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    with pytest.raises(Unauthenticated):
        login(db, auth, tenant, "alice", "wrong-password-1")
    event = audit_events(db, tenant, "auth.login")[-1]
    assert event.result == "failure"
    assert "alice@example.com" not in json.dumps(event.details)
    assert "wrong-password-1" not in json.dumps(event.details)


def test_account_locks_after_repeated_failures(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    for _ in range(5):
        with pytest.raises(Unauthenticated):
            login(db, auth, tenant, "alice", "wrong-password-1")
    with pytest.raises(Unauthenticated):
        login(db, auth, tenant, "alice")  # the right password, but the account is locked
    assert audit_events(db, tenant, "auth.account_locked")
    with db.tenant_session(tenant) as session:
        session.execute(update(User).values(locked_until=utcnow() - PAST))
    assert login(db, auth, tenant, "alice").state == "active"


def test_a_success_resets_the_failure_counter(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    for _ in range(3):
        with pytest.raises(Unauthenticated):
            login(db, auth, tenant, "alice", "wrong-password-1")
    login(db, auth, tenant, "alice")
    for _ in range(3):
        with pytest.raises(Unauthenticated):
            login(db, auth, tenant, "alice", "wrong-password-1")
    assert login(db, auth, tenant, "alice").state == "active"


def test_disabled_user_cannot_log_in(db: Database, auth: AuthService, tenant: uuid.UUID) -> None:
    new_user(db, tenant, "alice", status="disabled")
    with pytest.raises(Unauthenticated):
        login(db, auth, tenant, "alice")


def test_service_accounts_cannot_log_in_with_a_password(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "robot")
    with db.tenant_session(tenant) as session:
        session.execute(update(User).values(kind="service"))
    with pytest.raises(Unauthenticated):
        login(db, auth, tenant, "robot")


def test_login_is_rate_limited_per_address(
    db: Database, audit: AuditLog, keys: LocalKeyProvider, deny_limiter: Any, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    limited = AuthService(db, audit, keys, deny_limiter)
    with pytest.raises(RateLimited):
        limited.login(tenant_slug(db, tenant), email("alice"), STRONG, AuditActor(ip="203.0.113.9"))


def test_garbage_and_forged_tokens_are_rejected(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    real = login(db, auth, tenant, "alice").token
    for token in ("", "garbage", "abc.def", f"{tenant.hex}.forged", real + "x"):
        with pytest.raises(Unauthenticated):
            auth.authenticate_session(token)


def test_a_token_cannot_be_moved_to_another_tenant(
    db: Database, auth: AuthService, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    secret = login(db, auth, tenant, "alice").token.split(".", maxsplit=1)[1]
    with pytest.raises(Unauthenticated):
        auth.authenticate_session(f"{other_tenant.hex}.{secret}")


def test_logout_revokes_the_session(db: Database, auth: AuthService, tenant: uuid.UUID) -> None:
    new_user(db, tenant, "alice")
    token = login(db, auth, tenant, "alice").token
    auth.logout(auth.authenticate_session(token))
    with pytest.raises(Unauthenticated):
        auth.authenticate_session(token)
    assert audit_events(db, tenant, "auth.logout")


def test_expired_session_is_rejected(db: Database, auth: AuthService, tenant: uuid.UUID) -> None:
    new_user(db, tenant, "alice")
    token = login(db, auth, tenant, "alice").token
    with db.tenant_session(tenant) as session:
        session.execute(update(UserSession).values(expires_at=utcnow() - PAST))
    with pytest.raises(Unauthenticated):
        auth.authenticate_session(token)


def test_password_change_ends_other_sessions_but_keeps_this_one(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    first = login(db, auth, tenant, "alice").token
    second = login(db, auth, tenant, "alice").token
    auth.change_password(auth.authenticate_session(first), STRONG, "another-strong-pass-7")
    assert auth.authenticate_session(first)
    with pytest.raises(Unauthenticated):
        auth.authenticate_session(second)
    with pytest.raises(Unauthenticated):
        login(db, auth, tenant, "alice")
    assert login(db, auth, tenant, "alice", "another-strong-pass-7").state == "active"


def test_password_change_checks_the_current_password_and_policy(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    who = auth.authenticate_session(login(db, auth, tenant, "alice").token)
    with pytest.raises(DomainError) as wrong:
        auth.change_password(who, "not-the-password-1", "another-strong-pass-7")
    assert wrong.value.code == "bad_password"
    with pytest.raises(DomainError) as weak:
        auth.change_password(who, STRONG, "short")
    assert weak.value.code == "weak_password"


def test_mfa_setup_needs_a_valid_code_before_it_turns_on(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    user_id = new_user(db, tenant, "alice")
    who = auth.authenticate_session(login(db, auth, tenant, "alice").token)
    auth.begin_mfa_setup(who)
    with pytest.raises(DomainError):
        auth.confirm_mfa(who, "000000")
    with db.tenant_session(tenant) as session:
        user = session.get(User, user_id)
        assert user is not None
        assert user.mfa_enabled is False
    assert login(db, auth, tenant, "alice").state == "active"


def test_mfa_secret_is_not_stored_in_the_clear(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    who = auth.authenticate_session(login(db, auth, tenant, "alice").token)
    setup = auth.begin_mfa_setup(who)
    with db.tenant_session(tenant) as session:
        stored = session.scalar(select(MfaCredential.secret_encrypted))
    assert stored is not None
    assert setup.secret.encode() not in stored


def test_mfa_login_needs_a_second_factor(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    who = auth.authenticate_session(login(db, auth, tenant, "alice").token)
    secret, codes, step, _ = enable_mfa(auth, who)
    assert len(codes) == 10

    pending = login(db, auth, tenant, "alice")
    assert pending.state == "mfa_pending"
    with pytest.raises(Unauthenticated):
        auth.authenticate_session(pending.token)  # a pending session grants nothing

    next_code = pyotp.TOTP(secret).at((step + 1) * 30)
    active = auth.complete_mfa(pending.token, next_code)
    assert active.state == "active"
    assert auth.authenticate_session(active.token)
    with pytest.raises(Unauthenticated):
        auth.complete_mfa(pending.token, next_code)  # the pending session is spent


def test_a_totp_code_cannot_be_replayed(db: Database, auth: AuthService, tenant: uuid.UUID) -> None:
    new_user(db, tenant, "alice")
    who = auth.authenticate_session(login(db, auth, tenant, "alice").token)
    _, _, _, used_code = enable_mfa(auth, who)
    pending = login(db, auth, tenant, "alice")
    with pytest.raises(DomainError) as caught:
        auth.complete_mfa(pending.token, used_code)
    assert caught.value.code == "bad_mfa_code"


def test_wrong_mfa_code_fails_and_is_audited(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    who = auth.authenticate_session(login(db, auth, tenant, "alice").token)
    enable_mfa(auth, who)
    pending = login(db, auth, tenant, "alice")
    with pytest.raises(DomainError):
        auth.complete_mfa(pending.token, "000000")
    assert audit_events(db, tenant, "auth.mfa_failed")


def test_recovery_codes_work_exactly_once(
    db: Database, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    who = auth.authenticate_session(login(db, auth, tenant, "alice").token)
    _, codes, _, _ = enable_mfa(auth, who)
    assert auth.complete_mfa(login(db, auth, tenant, "alice").token, codes[0]).state == "active"
    with pytest.raises(DomainError):
        auth.complete_mfa(login(db, auth, tenant, "alice").token, codes[0])
    assert auth.complete_mfa(login(db, auth, tenant, "alice").token, codes[1]).state == "active"
    assert audit_events(db, tenant, "auth.mfa_verified")[0].details["method"] == "recovery_code"


def test_mfa_cannot_be_set_up_twice(db: Database, auth: AuthService, tenant: uuid.UUID) -> None:
    new_user(db, tenant, "alice")
    who = auth.authenticate_session(login(db, auth, tenant, "alice").token)
    enable_mfa(auth, who)
    with pytest.raises(Conflict):
        auth.begin_mfa_setup(who)


def test_audit_chain_stays_valid_through_authentication(
    db: Database, audit: AuditLog, auth: AuthService, tenant: uuid.UUID
) -> None:
    new_user(db, tenant, "alice")
    with pytest.raises(Unauthenticated):
        login(db, auth, tenant, "alice", "wrong-password-1")
    token = login(db, auth, tenant, "alice").token
    auth.logout(auth.authenticate_session(token))
    with db.tenant_session(tenant) as session:
        assert audit.verify(session, tenant).ok
