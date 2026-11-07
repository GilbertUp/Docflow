"""ADR 0003. These prove isolation at the database, independent of application filters."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from docuflow.platform.db import Base, Database
from docuflow.platform.models import SystemConfig, Tenant

pytestmark = pytest.mark.db


def add_config(db: Database, tenant_id: uuid.UUID) -> None:
    with db.tenant_session(tenant_id) as session:
        session.add(SystemConfig(tenant_id=tenant_id, key="k", value={"v": 1}))


def test_tenant_cannot_read_another_tenants_rows(
    db: Database, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    add_config(db, tenant)
    with db.tenant_session(other_tenant) as session:
        assert session.scalars(select(SystemConfig)).all() == []
    with db.tenant_session(tenant) as session:
        assert len(session.scalars(select(SystemConfig)).all()) == 1


def test_tenant_cannot_write_into_another_tenant(
    db: Database, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):  # noqa: PT012
        with db.tenant_session(tenant) as session:
            session.add(SystemConfig(tenant_id=other_tenant, key="x", value={}))
            session.flush()


def test_tenant_cannot_change_rows_of_another_tenant(
    db: Database, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    add_config(db, tenant)
    with db.tenant_session(other_tenant) as session:
        result = session.execute(text("UPDATE system_config SET key = 'stolen'"))
        assert result.rowcount == 0  # type: ignore[attr-defined]


def test_missing_tenant_setting_fails_closed(db: Database, tenant: uuid.UUID) -> None:
    add_config(db, tenant)
    with db.unscoped_session() as session:
        assert session.scalars(select(SystemConfig)).all() == []
        assert session.scalars(select(Tenant)).all() == []


def test_tenant_setting_does_not_leak_between_transactions(db: Database, tenant: uuid.UUID) -> None:
    add_config(db, tenant)
    with db.tenant_session(tenant) as session:
        session.scalars(select(SystemConfig)).all()
    with db.unscoped_session() as session:
        assert session.scalars(select(SystemConfig)).all() == []


def test_tenant_session_rejects_client_strings(db: Database, tenant: uuid.UUID) -> None:
    with pytest.raises(TypeError), db.tenant_session(str(tenant)):  # type: ignore[arg-type]
        pass


def test_runtime_role_cannot_create_tenants(db: Database, tenant: uuid.UUID) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):  # noqa: PT012
        with db.tenant_session(tenant) as session:
            session.add(Tenant(name="rogue", slug="rogue"))
            session.flush()


def test_runtime_role_cannot_bypass_rls(engines: dict[str, Engine]) -> None:
    with engines["app"].connect() as connection:
        row = connection.execute(
            text("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert (row.rolbypassrls, row.rolsuper) == (False, False)


def test_every_tenant_table_has_forced_rls_and_a_policy(engines: dict[str, Engine]) -> None:
    """Fails as soon as a future migration adds a tenant table without protection."""
    with engines["admin"].connect() as connection:
        protected = {
            row.relname
            for row in connection.execute(
                text(
                    """
                    SELECT c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r'
                      AND c.relrowsecurity AND c.relforcerowsecurity
                      AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)
                    """
                )
            )
        }
        columns = inspect(connection)
        tenant_tables = {
            name
            for name in columns.get_table_names(schema="public")
            if name == "tenant"
            or any(column["name"] == "tenant_id" for column in columns.get_columns(name))
        }
    assert tenant_tables, "no tables found"
    assert tenant_tables - protected == set()


def test_models_and_migrations_agree(engines: dict[str, Engine]) -> None:
    with engines["admin"].connect() as connection:
        tables = set(inspect(connection).get_table_names(schema="public"))
    assert set(Base.metadata.tables) <= tables
