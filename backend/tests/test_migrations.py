"""Rollback and re-upgrade on a separate database, so other tests are not disturbed."""

from __future__ import annotations

import os

import psycopg
import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, make_url

from tests.conftest import DB_NAME, alembic_config, bootstrap_sql

pytestmark = pytest.mark.db


def test_models_match_migrations(migrated: str) -> None:
    """Fails if a model changes without a migration, or the migration drifts from the model."""
    command.check(alembic_config(migrated))


def test_upgrade_downgrade_upgrade(admin_url: str, migrator_url: str, migrated: str) -> None:
    assert migrated
    plain = make_url(admin_url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(plain, autocommit=True) as connection:
        connection.execute("DROP DATABASE IF EXISTS docuflow_migrate WITH (FORCE)")
        connection.execute("CREATE DATABASE docuflow_migrate")
    scratch = make_url(plain).set(database="docuflow_migrate")
    with psycopg.connect(
        scratch.render_as_string(hide_password=False), autocommit=True
    ) as connection:
        embedded = "DOCUFLOW_TEST_ADMIN_URL" not in os.environ
        connection.execute(bootstrap_sql(connection, embedded=embedded))

    url = migrator_url.replace(f"/{DB_NAME}", "/docuflow_migrate")
    config = alembic_config(url)
    engine = create_engine(url)
    try:
        command.upgrade(config, "head")
        assert "audit_event" in inspect(engine).get_table_names()
        command.downgrade(config, "base")
        assert "audit_event" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert "audit_event" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
