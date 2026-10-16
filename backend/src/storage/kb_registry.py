"""Persisted definitions of knowledge bases created at runtime.

One table in the DATABASE_URL database (not in any `kb_<name>` database), so
the list survives restarts and lives next to the server's admin connection.
Each created knowledge base still stores its data in its own database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from backend.src.storage.knowledge_store import StorageError


REGISTRY_TABLE = "knowledge_base_registry"
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class KnowledgeBaseExistsError(StorageError):
    """A knowledge base with this name is already registered."""


@dataclass(frozen=True)
class RegisteredKnowledgeBase:
    name: str
    title: str
    description: str
    template: str
    created_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name, "title": self.title, "description": self.description,
            "template": self.template, "created_at": self.created_at.isoformat(),
        }


class KnowledgeBaseRegistry:
    """Reads and adds rows of the registry table, creating it on first use."""

    def __init__(self, engine: AsyncEngine, *, schema: str = "public") -> None:
        if not _SCHEMA_RE.match(schema):
            raise ValueError(f"Invalid schema name: {schema!r}")
        self._engine = engine
        self._table = f'"{schema}".{REGISTRY_TABLE}'
        self._ready = False

    @classmethod
    def from_url(cls, database_url: str, *, schema: str = "public") -> "KnowledgeBaseRegistry":
        return cls(create_async_engine(database_url, pool_pre_ping=True), schema=schema)

    async def _ensure_table(self) -> None:
        if self._ready:
            return
        async with self._engine.begin() as conn:
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                    name text PRIMARY KEY,
                    title text NOT NULL,
                    description text NOT NULL DEFAULT '',
                    template text NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now()
                )
            """))
        self._ready = True

    async def list(self) -> list[RegisteredKnowledgeBase]:
        try:
            await self._ensure_table()
            async with self._engine.connect() as conn:
                rows = (await conn.execute(text(
                    f"SELECT name, title, description, template, created_at "
                    f"FROM {self._table} ORDER BY created_at, name"
                ))).mappings().all()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read the knowledge base registry: {exc}") from exc
        return [RegisteredKnowledgeBase(**row) for row in rows]

    async def add(
        self, *, name: str, title: str, description: str, template: str
    ) -> RegisteredKnowledgeBase:
        try:
            await self._ensure_table()
            async with self._engine.begin() as conn:
                row = (await conn.execute(text(
                    f"INSERT INTO {self._table} (name, title, description, template) "
                    "VALUES (:name, :title, :description, :template) "
                    "RETURNING name, title, description, template, created_at"
                ), {"name": name, "title": title, "description": description,
                    "template": template})).mappings().one()
        except IntegrityError as exc:
            raise KnowledgeBaseExistsError(f"Knowledge base {name!r} already exists") from exc
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not register knowledge base {name!r}: {exc}") from exc
        return RegisteredKnowledgeBase(**row)

    async def aclose(self) -> None:
        await self._engine.dispose()
