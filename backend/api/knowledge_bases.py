"""Authenticated knowledge-base catalog backed by the application database."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from backend.api.auth import require_current_user
from backend.knowledge_bases.base import KnowledgeBaseSpec, Retrieval
from backend.knowledge_bases.custom import DEFAULT_TEMPLATE, MAX_DESCRIPTION_LENGTH, MAX_TITLE_LENGTH, TEMPLATES, custom_spec
from backend.src.services.auth import Account
from backend.src.storage.job_queue import PgListener
from backend.src.storage.orm import UserRole
from backend.src.storage.repositories.base import KnowledgeStorageUnavailable
from backend.src.storage.repositories.knowledge_bases import KnowledgeBaseDetail, KnowledgeBaseEntry, KnowledgeBaseLimitReached, KnowledgeBaseNameTaken, KnowledgeBaseRepository

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

    def remove(self, name: str) -> None: ...


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
        self._watcher: asyncio.Task[None] | None = None

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

    async def refresh(self) -> None:
        """Reconcile this process with committed catalog changes before serving requests."""

        async with self._lock:
            rows = {row.name: row for row in await self._repository.list_active()}
            for name in self._entries.keys() - rows.keys():
                self._remove(name)
            for name, row in rows.items():
                if self._entries.get(name) != row:
                    self._add(row)
            self._loaded = True

    async def detail(self, name: str) -> KnowledgeBaseDetail:
        """Return details for an active name or raise HTTP 404."""

        detail = await self._repository.detail(name)
        if detail is None:
            raise HTTPException(404, "Knowledge base not found")
        return detail

    async def rename(
        self, name: str, *, title: str | None, description: str | None,
    ) -> KnowledgeBaseDetail:
        """Change the display fields and publish the current catalog."""

        async with self._lock:
            updated = await self._repository.rename(name, title=title, description=description)
            if updated is None:
                raise HTTPException(404, "Knowledge base not found")
            row, detail = updated
            self._add(row)
            return detail

    async def delete(self, name: str) -> None:
        """Remove a KB from the active catalog and notify local services."""

        async with self._lock:
            if not await self._repository.delete(name):
                raise HTTPException(404, "Knowledge base not found")
            self._remove(name)

    def _remove(self, name: str) -> None:
        """Evict a catalog entry and stop its services from accepting new requests."""

        if self._entries.pop(name, None) is not None:
            del self._specs[name]
            for listener in self._listeners:
                listener.remove(name)

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

    def resolve_id(self, knowledge_base_id: UUID) -> tuple[str, KnowledgeBaseSpec] | None:
        """Return the cached name and policy spec for an active database ID."""

        for name, row in self._entries.items():
            if row.id == knowledge_base_id:
                return name, self._specs[name]
        return None

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
            async with self._lock:
                row = await self._repository.create(
                    name=spec.name, title=spec.title, description=spec.description,
                    template=template, created_by=created_by, limit=self._max_created,
                )
                self._add(row)
        except KnowledgeBaseNameTaken as exc:
            raise KnowledgeBaseConflict(f"Knowledge base {name!r} already exists") from exc
        except KnowledgeBaseLimitReached as exc:
            raise KnowledgeBaseLimit(str(exc)) from exc
        return self._json(row, spec)

    def start_listener(self, database_url: str) -> None:
        """Start listening for catalog writes by other processes."""

        self._watcher = asyncio.create_task(self._watch(self._repository.listener(database_url)))

    async def _watch(self, listener: PgListener) -> None:
        """Refresh on notification and periodically recover missed notifications."""

        try:
            while True:
                await listener.wait(30)
                try:
                    await self.refresh()
                except KnowledgeStorageUnavailable:
                    logger.warning("Could not refresh the knowledge base catalog", exc_info=True)
        finally:
            await listener.aclose()

    async def aclose(self) -> None:
        """Stop the catalog listener and close its database connection."""

        if self._watcher is not None:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
            self._watcher = None


async def refresh_catalog(request: Request) -> None:
    """Reconcile catalog listeners before a protected request uses their KBs."""

    catalog = getattr(request.app.state, "kb_catalog", None)
    if catalog is not None:
        try:
            await catalog.refresh()
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
        await catalog.refresh()
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


class UpdateKnowledgeBase(BaseModel):
    """Display fields accepted by PATCH. Omitted fields keep their values."""

    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_TITLE_LENGTH,
    )] | None = None
    description: Annotated[str, StringConstraints(
        strip_whitespace=True, max_length=MAX_DESCRIPTION_LENGTH,
    )] | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> UpdateKnowledgeBase:
        """Require at least one non-null display field."""

        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("Provide a title and/or description")
        return self


@router.get("/{kb}")
async def knowledge_base_detail(
    request: Request, kb: str, _account: Annotated[Account, Depends(require_current_user)],
) -> KnowledgeBaseDetail:
    """Show metadata and processing counts for an active knowledge base."""

    return await _catalog(request).detail(kb)


@router.patch("/{kb}")
async def update_knowledge_base(
    request: Request, kb: str, body: UpdateKnowledgeBase,
    _account: Annotated[Account, Depends(require_expert)],
) -> KnowledgeBaseDetail:
    """Change the display title and/or description."""

    return await _catalog(request).rename(kb, title=body.title, description=body.description)


@router.delete("/{kb}", status_code=204)
async def delete_knowledge_base(
    request: Request, kb: str, account: Annotated[Account, Depends(require_current_user)],
) -> Response:
    """Soft-delete a knowledge base as an administrator."""

    if account.role != UserRole.ADMIN.value:
        raise HTTPException(403, "Administrator role required")
    await _catalog(request).delete(kb)
    return Response(status_code=204)
