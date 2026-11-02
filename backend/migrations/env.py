"""Alembic environment for the backend application tables.

The database comes from, in order: `config.attributes["database_url"]` (set by
code that calls Alembic, such as tests), MIGRATION_DATABASE_URL (a setup-owner
account), then APP_DATABASE_URL. Values in the repository `.env` are loaded
first. DATABASE_URL is never used: its database holds the running service's own
`ingestion_jobs` table, which has the same name as the new one.

Only tables defined in `backend.src.storage.orm` are compared by autogenerate;
tables created by other tools in the same database are ignored.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from alembic import context
from dotenv import load_dotenv
from pgvector.sqlalchemy import Vector
from sqlalchemy import create_engine, pool

from backend.src.storage.orm import Base


# Created by a hand-written revision, because it is only possible when the
# embedding size chosen at migration time is at most 2000.
MIGRATION_ONLY_INDEXES = frozenset({"ix_knowledge_chunks_embedding_hnsw"})

config = context.config
target_metadata = Base.metadata

# Settings live in pyproject.toml, so there is no alembic.ini logging section.
# Callers such as tests can keep their own logging by setting this attribute.
if config.attributes.get("configure_logging", True):
    logging.basicConfig(format="%(levelname)-5.5s [%(name)s] %(message)s")
    logging.getLogger("alembic").setLevel(logging.INFO)


def database_url() -> str:
    """Select the database URL migrations connect to.

    Returns:
        The configured SQLAlchemy URL.

    Raises:
        ValueError: No database URL is configured.
    """

    load_dotenv()
    url = (
        config.attributes.get("database_url")
        or os.getenv("MIGRATION_DATABASE_URL")
        or os.getenv("APP_DATABASE_URL")
    )
    if not url:
        raise ValueError(
            "Missing database configuration: set APP_DATABASE_URL "
            "(or MIGRATION_DATABASE_URL) to a database separate from DATABASE_URL"
        )
    return url


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Limit autogenerate to tables and indexes owned by these migrations."""

    if type_ == "table" and reflected and compare_to is None:
        return False
    if type_ == "index" and name in MIGRATION_ONLY_INDEXES:
        return False
    return True


def compare_type(
    context: Any,
    inspected_column: Any,
    metadata_column: Any,
    inspected_type: Any,
    metadata_type: Any,
) -> bool | None:
    """Skip embedding size comparisons; the size is chosen at migration time."""

    if isinstance(metadata_type, Vector):
        return False
    return None


def render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    """Render embedding columns so the size is read when the revision runs.

    Args:
        type_: Kind of item autogenerate is rendering.
        obj: The item itself, such as a column type.
        autogen_context: Collects imports needed by the revision file.

    Returns:
        Python source for `Vector` types; False to use the default rendering
        for everything else.
    """

    if type_ == "type" and isinstance(obj, Vector):
        autogen_context.imports.add("from pgvector.sqlalchemy import Vector")
        autogen_context.imports.add(
            "from backend.src.storage.embeddings import embedding_dim_from_env"
        )
        return "Vector(embedding_dim_from_env())"
    return False


def configure_options() -> dict[str, Any]:
    return {
        "target_metadata": target_metadata,
        "include_object": include_object,
        "compare_type": compare_type,
        "render_item": render_item,
        "compare_server_default": True,
    }


def run_migrations_offline() -> None:
    """Write migration SQL to standard output without connecting."""

    context.configure(
        url=database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **configure_options(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations through a new connection, in one transaction."""

    engine = create_engine(database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, **configure_options())
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
