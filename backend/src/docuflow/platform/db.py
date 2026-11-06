"""Database engines and tenant-scoped sessions (ADR 0002, ADR 0003)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import DateTime, Engine, MetaData, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from docuflow.platform.ids import uuid7

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, future=True)


class Database:
    """Opens sessions. All tenant work goes through tenant_session, which fails closed."""

    def __init__(self, engine: Engine, platform_engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._platform_sessions = sessionmaker(platform_engine, expire_on_commit=False)

    @contextmanager
    def tenant_session(self, tenant_id: uuid.UUID) -> Iterator[Session]:
        """One transaction bound to one tenant. Commits on success, rolls back on error."""
        if not isinstance(tenant_id, uuid.UUID):
            raise TypeError("tenant_id must be a UUID taken from the authenticated principal")
        with self._sessions() as session, session.begin():
            # is_local=true scopes the value to this transaction, which is PgBouncer-safe.
            session.execute(
                text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": str(tenant_id)},
            )
            yield session

    @contextmanager
    def unscoped_session(self) -> Iterator[Session]:
        """Runtime role without a tenant. RLS returns no rows; used for health checks."""
        with self._sessions() as session, session.begin():
            yield session

    @contextmanager
    def platform_session(self) -> Iterator[Session]:
        """Cross-tenant session for tenant creation and the dispatcher. Never for requests."""
        with self._platform_sessions() as session, session.begin():
            yield session
