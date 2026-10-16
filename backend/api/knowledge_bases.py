"""Knowledge base catalog: the built-in knowledge bases plus ones created in the UI.

A created knowledge base copies a template's policy (see
`backend.knowledge_bases.custom`), gets its own `kb_<name>` database and is
recorded in the registry table, so it is rebuilt after a restart. The catalog
hands every spec to the services that serve knowledge bases (QA agents and
capture), so a new one is usable without restarting the API.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.knowledge_bases.base import KnowledgeBaseSpec, Retrieval
from backend.knowledge_bases.custom import (
    DEFAULT_TEMPLATE,
    MAX_DESCRIPTION_LENGTH,
    MAX_TITLE_LENGTH,
    TEMPLATES,
    custom_spec,
)
from backend.src.storage import StorageError
from backend.src.storage import databases
from backend.src.storage.kb_registry import (
    KnowledgeBaseExistsError,
    KnowledgeBaseRegistry,
    RegisteredKnowledgeBase,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/knowledge-bases", tags=["knowledge-bases"])


class KnowledgeBaseConflict(ValueError):
    """The name, or its database, is already taken."""


class KnowledgeBaseLimit(ValueError):
    """KB_MAX_CREATED knowledge bases already exist."""


class SpecListener(Protocol):
    def add(self, spec: KnowledgeBaseSpec) -> None: ...


def max_created_from_env() -> int:
    # An empty value (as in .env.example) means the default.
    return max(0, int(os.getenv("KB_MAX_CREATED") or "50"))


def _server_url() -> str:
    try:
        return databases.server_url_from_env()
    except ValueError as exc:
        # A configuration problem, not a bad request.
        raise databases.DatabaseAdminError(str(exc)) from exc


def _default_registry() -> KnowledgeBaseRegistry:
    return KnowledgeBaseRegistry.from_url(_server_url())


class DatabaseAdmin:
    """Database checks on the DATABASE_URL server; replaced by fakes in tests."""

    async def exists(self, database: str) -> bool:
        return await databases.database_exists(_server_url(), database)

    async def create(self, database: str) -> None:
        await databases.ensure_database(_server_url(), database)

    async def drop(self, database: str) -> None:
        await databases.drop_database(_server_url(), database)


class KnowledgeBaseCatalog:
    def __init__(
        self,
        builtins: Mapping[str, KnowledgeBaseSpec],
        *,
        listeners: Iterable[SpecListener] = (),
        registry_factory: Callable[[], Any] = _default_registry,
        database_admin: Any = None,
        max_created: int | None = None,
    ) -> None:
        self._builtins = dict(builtins)
        self._created: dict[str, tuple[KnowledgeBaseSpec, RegisteredKnowledgeBase]] = {}
        self._listeners = list(listeners)
        self._registry_factory = registry_factory
        self._registry: Any = None
        self._admin = database_admin or DatabaseAdmin()
        self._max_created = max_created_from_env() if max_created is None else max_created
        self._loaded = False
        self._lock = asyncio.Lock()

    def _registry_store(self) -> Any:
        if self._registry is None:
            self._registry = self._registry_factory()
        return self._registry

    async def ensure_loaded(self) -> None:
        """Reads the registry once; later calls are free."""

        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            for row in await self._registry_store().list():
                if row.name in self._builtins:
                    logger.warning("Registered knowledge base %r shadows a built-in one; skipped", row.name)
                    continue
                try:
                    spec = custom_spec(row.name, title=row.title, description=row.description,
                                       template=row.template)
                except ValueError:
                    logger.warning("Registered knowledge base %r is invalid; skipped", row.name,
                                   exc_info=True)
                    continue
                self._add(spec, row)
            self._loaded = True

    def _add(self, spec: KnowledgeBaseSpec, row: RegisteredKnowledgeBase) -> None:
        self._created[spec.name] = (spec, row)
        for listener in self._listeners:
            listener.add(spec)

    def items(self) -> list[dict[str, Any]]:
        builtins = [self._json(spec) for _, spec in sorted(self._builtins.items())]
        created = sorted(
            (self._json(spec, row) for spec, row in self._created.values()),
            key=lambda item: item["title"].casefold(),
        )
        return builtins + created

    @staticmethod
    def _json(spec: KnowledgeBaseSpec, row: RegisteredKnowledgeBase | None = None) -> dict[str, Any]:
        return {
            "name": spec.name,
            "title": spec.title or spec.name,
            "description": spec.description.strip(),
            "builtin": row is None,
            "template": row.template if row else None,
            "created_at": row.created_at.isoformat() if row else None,
            # Capture extracts records, which only knowledge retrieval searches.
            "capture": spec.retrieval is Retrieval.KNOWLEDGE,
        }

    async def create(
        self, *, name: str, title: str, description: str = "", template: str = DEFAULT_TEMPLATE
    ) -> dict[str, Any]:
        """Creates the database, registers the knowledge base and serves it."""

        await self.ensure_loaded()
        spec = custom_spec(name, title=title, description=description, template=template)
        async with self._lock:
            if name in self._builtins or name in self._created:
                raise KnowledgeBaseConflict(f"Knowledge base {name!r} already exists")
            if len(self._created) >= self._max_created:
                raise KnowledgeBaseLimit(f"At most {self._max_created} knowledge bases can be created")
            # An unregistered database may hold another knowledge base's data.
            if await self._admin.exists(spec.database):
                raise KnowledgeBaseConflict(f"Database {spec.database!r} already exists")
            await self._admin.create(spec.database)
            try:
                row = await self._registry_store().add(
                    name=spec.name, title=spec.title, description=spec.description,
                    template=template,
                )
            except BaseException:
                await self._admin.drop(spec.database)
                raise
            self._add(spec, row)
        return self._json(spec, row)

    async def aclose(self) -> None:
        if self._registry is not None:
            await self._registry.aclose()


async def refresh_catalog(request: Request) -> None:
    """Loads created knowledge bases before a list request, if not done yet."""

    catalog = getattr(request.app.state, "kb_catalog", None)
    if catalog is None:
        return
    try:
        await catalog.ensure_loaded()
    except Exception:
        # Built-in knowledge bases still work without the registry.
        logger.warning("Could not load created knowledge bases", exc_info=True)


def _catalog(request: Request) -> KnowledgeBaseCatalog:
    catalog = getattr(request.app.state, "kb_catalog", None)
    if catalog is None:
        raise HTTPException(503, "The knowledge base catalog is not configured")
    return catalog


class CreateKnowledgeBase(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    description: str = Field("", max_length=MAX_DESCRIPTION_LENGTH)
    template: str = DEFAULT_TEMPLATE


@router.get("")
async def list_knowledge_bases(request: Request) -> dict[str, Any]:
    catalog = _catalog(request)
    try:
        await catalog.ensure_loaded()
    except (StorageError, databases.DatabaseAdminError) as exc:
        logger.warning("Could not load created knowledge bases", exc_info=True)
        raise HTTPException(
            503, f"The knowledge base registry is unavailable: {type(exc).__name__}"
        ) from exc
    return {
        "knowledge_bases": catalog.items(),
        "templates": [
            {"name": name, "description": spec.description.strip()}
            for name, spec in TEMPLATES.items()
        ],
    }


@router.post("", status_code=201)
async def create_knowledge_base(request: Request, body: CreateKnowledgeBase) -> dict[str, Any]:
    catalog = _catalog(request)
    try:
        return await catalog.create(
            name=body.name.strip(), title=body.title, description=body.description,
            template=body.template,
        )
    except (KnowledgeBaseConflict, KnowledgeBaseExistsError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except KnowledgeBaseLimit as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (StorageError, databases.DatabaseAdminError) as exc:
        logger.warning("Creating knowledge base %r failed", body.name, exc_info=True)
        raise HTTPException(503, f"Could not create the knowledge base: {type(exc).__name__}") from exc
