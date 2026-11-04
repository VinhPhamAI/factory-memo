"""Authenticated knowledge-base catalog backed by the application database."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api.auth import require_current_user
from backend.knowledge_bases.base import KnowledgeBaseSpec, Retrieval
from backend.knowledge_bases.custom import DEFAULT_TEMPLATE, MAX_DESCRIPTION_LENGTH, MAX_TITLE_LENGTH, TEMPLATES, custom_spec
from backend.src.services.auth import Account
from backend.src.storage.orm import UserRole
from backend.src.storage.repositories.base import KnowledgeStorageUnavailable
from backend.src.storage.repositories.knowledge_bases import KnowledgeBaseEntry, KnowledgeBaseLimitReached, KnowledgeBaseNameTaken, KnowledgeBaseRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/knowledge-bases", tags=["knowledge-bases"])
RESERVED_NAMES = frozenset({"industrial_qa", "general"})


class KnowledgeBaseConflict(ValueError):
    """The requested name is already used or reserved."""


class KnowledgeBaseLimit(ValueError):
    """The configured number of active knowledge bases was reached."""


class SpecListener(Protocol):
    """Service that accepts a knowledge-base policy at runtime."""

    def add(self, spec: KnowledgeBaseSpec) -> None: ...


def max_created_from_env() -> int:
    """Return the configured limit, treating an empty setting as 50."""

    return max(0, int(os.getenv("KB_MAX_CREATED") or "50"))


class KnowledgeBaseCatalog:
    """Cache database catalog rows and publish their policy specs to listeners."""

    def __init__(self, repository: KnowledgeBaseRepository, *, listeners: Iterable[SpecListener] = (), max_created: int | None = None) -> None:
        self._repository = repository
        self._entries: dict[str, KnowledgeBaseEntry] = {}
        self._specs: dict[str, KnowledgeBaseSpec] = {}
        self._listeners = list(listeners)
        self._max_created = max_created_from_env() if max_created is None else max_created
        self._loaded = False
        self._lock = asyncio.Lock()

    async def ensure_loaded(self) -> None:
        """Load active rows once and notify listeners of every valid policy."""

        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            for row in await self._repository.list_active():
                self._add(row)
            self._loaded = True

    def _add(self, row: KnowledgeBaseEntry) -> None:
        spec = custom_spec(row.name, title=row.title, description=row.description, template=row.template)
        self._entries[row.name] = row
        self._specs[row.name] = spec
        for listener in self._listeners:
            listener.add(spec)

    def resolve(self, name: str) -> tuple[UUID, KnowledgeBaseSpec] | None:
        """Return the cached database ID and policy spec for an active name."""

        row = self._entries.get(name)
        return (row.id, self._specs[name]) if row is not None else None

    def names(self) -> list[str]:
        """Return active database-backed names in stable order."""

        return sorted(self._entries)

    def add_listener(self, listener: SpecListener) -> None:
        """Register a runtime service before the initial catalog load."""

        self._listeners.append(listener)

    def items(self) -> list[dict[str, Any]]:
        """Return public catalog rows without built-in policy packages."""

        return sorted((self._json(row, self._specs[row.name]) for row in self._entries.values()), key=lambda item: item["title"].casefold())

    @staticmethod
    def _json(row: KnowledgeBaseEntry, spec: KnowledgeBaseSpec) -> dict[str, Any]:
        return {"name": row.name, "title": row.title, "description": row.description.strip(), "builtin": False, "template": row.template, "created_at": row.created_at.isoformat(), "capture": spec.retrieval is Retrieval.KNOWLEDGE}

    async def create(self, *, name: str, title: str, created_by: UUID, description: str = "", template: str = DEFAULT_TEMPLATE) -> dict[str, Any]:
        """Validate and insert a catalog row, then publish its policy spec."""

        await self.ensure_loaded()
        if name in RESERVED_NAMES:
            raise KnowledgeBaseConflict(f"Knowledge base {name!r} is reserved")
        spec = custom_spec(name, title=title, description=description, template=template)
        try:
            row = await self._repository.create(name=spec.name, title=spec.title, description=spec.description, template=template, created_by=created_by, limit=self._max_created)
        except KnowledgeBaseNameTaken as exc:
            raise KnowledgeBaseConflict(f"Knowledge base {name!r} already exists") from exc
        except KnowledgeBaseLimitReached as exc:
            raise KnowledgeBaseLimit(str(exc)) from exc
        async with self._lock:
            self._add(row)
        return self._json(row, spec)

    async def aclose(self) -> None:
        """Keep the listener lifecycle interface used by the application."""


async def refresh_catalog(request: Request) -> None:
    """Ensure the database catalog has been loaded for a protected request."""

    catalog = getattr(request.app.state, "kb_catalog", None)
    if catalog is not None:
        try:
            await catalog.ensure_loaded()
        except KnowledgeStorageUnavailable as exc:
            raise HTTPException(
                503, "The knowledge base catalog is unavailable"
            ) from exc


def _catalog(request: Request) -> KnowledgeBaseCatalog:
    catalog = getattr(request.app.state, "kb_catalog", None)
    if catalog is None:
        raise HTTPException(503, "The knowledge base catalog is not configured")
    return catalog


def require_expert(account: Annotated[Account, Depends(require_current_user)]) -> Account:
    """Allow catalog writes only for active experts and administrators."""

    if account.role not in {UserRole.EXPERT.value, UserRole.ADMIN.value}:
        raise HTTPException(403, "Expert or administrator role required")
    return account


class CreateKnowledgeBase(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    description: str = Field("", max_length=MAX_DESCRIPTION_LENGTH)
    template: str = DEFAULT_TEMPLATE


@router.get("")
async def list_knowledge_bases(request: Request, _account: Annotated[Account, Depends(require_current_user)]) -> dict[str, Any]:
    catalog = _catalog(request)
    try:
        await catalog.ensure_loaded()
    except KnowledgeStorageUnavailable as exc:
        raise HTTPException(503, "The knowledge base catalog is unavailable") from exc
    return {"knowledge_bases": catalog.items(), "templates": [{"name": name, "description": spec.description.strip()} for name, spec in TEMPLATES.items()]}


@router.post("", status_code=201)
async def create_knowledge_base(request: Request, body: CreateKnowledgeBase, account: Annotated[Account, Depends(require_expert)]) -> dict[str, Any]:
    catalog = _catalog(request)
    try:
        return await catalog.create(name=body.name.strip(), title=body.title, description=body.description, template=body.template, created_by=account.id)
    except KnowledgeBaseConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except KnowledgeBaseLimit as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except KnowledgeStorageUnavailable as exc:
        logger.warning("Creating knowledge base %r failed", body.name, exc_info=True)
        raise HTTPException(503, "Could not create the knowledge base") from exc
