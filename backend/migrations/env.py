"""Alembic environment. Runs as docuflow_migrator, which owns the schema."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

from docuflow.modules.identity import models as identity_models  # noqa: F401
from docuflow.modules.repository import models as repository_models  # noqa: F401
from docuflow.platform import models  # noqa: F401  # registers tables on the metadata
from docuflow.platform.db import Base

target_metadata = Base.metadata


def database_url() -> str:
    url = context.config.get_main_option("sqlalchemy.url") or os.environ.get(
        "DOCUFLOW_MIGRATION_DATABASE_URL"
    )
    if not url:
        raise RuntimeError("Set DOCUFLOW_MIGRATION_DATABASE_URL to run migrations")
    return url


def run_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


def run_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
