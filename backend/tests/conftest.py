"""Test fixtures. DB tests use real PostgreSQL, never SQLite, because RLS is what is under test.

CI points DOCUFLOW_TEST_ADMIN_URL at a service container. Without it, pgserver provides an
embedded PostgreSQL with pgvector, so the suite still runs on machines without Docker.
"""

from __future__ import annotations

import base64
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url

from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from docuflow.platform.models import Tenant

BACKEND = Path(__file__).resolve().parents[1]
BOOTSTRAP_SQL = BACKEND.parent / "deploy" / "postgres" / "bootstrap.sql"
ROLE_PASSWORD = "test-only-password"
DB_NAME = "docuflow_test"


def _admin_url(tmp_root: Path) -> tuple[str, object | None]:
    configured = os.environ.get("DOCUFLOW_TEST_ADMIN_URL")
    if configured:
        return configured, None
    pgserver = pytest.importorskip("pgserver", reason="install the embedded-pg extra")
    server = pgserver.get_server(tmp_root / "pg", cleanup_mode="stop")
    return server.get_uri(), server


def bootstrap_sql(connection: psycopg.Connection[Any], *, embedded: bool) -> str:
    """Bootstrap SQL. The embedded server lacks some contrib extensions, so only it skips them.

    Tests that need a skipped extension must be marked so they run in CI. A real server never
    skips anything here, so a missing extension there still fails.
    """
    sql = BOOTSTRAP_SQL.read_text(encoding="utf-8")
    if not embedded:
        return sql
    available = {row[0] for row in connection.execute("SELECT name FROM pg_available_extensions")}
    lines = [
        line
        for line in sql.splitlines()
        if not (
            line.startswith("CREATE EXTENSION") and line.rstrip(";").split()[-1] not in available
        )
    ]
    return "\n".join(lines)


def _role_url(admin_url: str, role: str) -> str:
    url = make_url(admin_url).set(
        drivername="postgresql+psycopg",
        username=role,
        password=ROLE_PASSWORD,
        database=DB_NAME,
    )
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def admin_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    url, server = _admin_url(tmp_path_factory.mktemp("postgres"))
    plain = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(plain, autocommit=True) as connection:
        connection.execute(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")
        connection.execute(f"CREATE DATABASE {DB_NAME}")
    target = make_url(plain).set(database=DB_NAME).render_as_string(hide_password=False)
    with psycopg.connect(target, autocommit=True) as connection:
        connection.execute(bootstrap_sql(connection, embedded=server is not None))
        for role in ("docuflow_migrator", "docuflow_app", "docuflow_platform"):
            connection.execute(f"ALTER ROLE {role} LOGIN PASSWORD '{ROLE_PASSWORD}'")
    yield target
    if server is not None:
        server.cleanup()


@pytest.fixture(scope="session")
def migrator_url(admin_url: str) -> str:
    return _role_url(admin_url, "docuflow_migrator")


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def migrated(migrator_url: str) -> str:
    command.upgrade(alembic_config(migrator_url), "head")
    return migrator_url


@pytest.fixture(scope="session")
def engines(admin_url: str, migrated: str) -> Iterator[dict[str, Engine]]:
    created = {
        "app": create_engine(_role_url(admin_url, "docuflow_app")),
        "platform": create_engine(_role_url(admin_url, "docuflow_platform")),
        "admin": create_engine(make_url(admin_url).set(drivername="postgresql+psycopg")),
    }
    yield created
    for engine in created.values():
        engine.dispose()


@pytest.fixture(scope="session")
def db(engines: dict[str, Engine]) -> Database:
    return Database(engines["app"], engines["platform"])


@pytest.fixture(scope="session")
def audit_key() -> bytes:
    return base64.b64decode(base64.b64encode(os.urandom(32)))


@pytest.fixture(scope="session")
def audit(audit_key: bytes) -> AuditLog:
    return AuditLog(audit_key)


def _make_tenant(db: Database) -> uuid.UUID:
    with db.platform_session() as session:
        name = f"tenant-{uuid.uuid4()}"
        tenant = Tenant(name=name, slug=name)
        session.add(tenant)
        session.flush()
        return tenant.id


@pytest.fixture
def tenant(db: Database) -> uuid.UUID:
    return _make_tenant(db)


@pytest.fixture
def other_tenant(db: Database) -> uuid.UUID:
    return _make_tenant(db)


@pytest.fixture(scope="session")
def keys() -> LocalKeyProvider:
    return LocalKeyProvider(os.urandom(32))
