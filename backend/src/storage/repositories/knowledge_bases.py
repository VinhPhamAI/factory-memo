"""Knowledge-base rows: the collections the API serves."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.storage.job_queue import PgListener
from sqlalchemy.exc import IntegrityError

from backend.src.storage.orm import (
    ChunkStatus, KnowledgeBase, KnowledgeChunk, KnowledgeSource, SourceStatus, User,
)
from backend.src.storage.repositories.lifecycle import KB_DELETED, fail_queued
from backend.src.storage.repositories.base import Repository, violated_constraint


CATALOG_CHANNEL = "knowledge_base_catalog"
ACTIVE_NAME_UNIQUE_INDEX = "ux_knowledge_bases_active_name"
# Serializes the limit check and the insert of concurrent creations.
_CREATE_LOCK_KEY = "knowledge_bases:create"
_DEFAULT_LOCK_KEY = "knowledge_bases:default"


class KnowledgeBaseNameTaken(ValueError):
    """Another active knowledge base already uses the name."""


class KnowledgeBaseLimitReached(ValueError):
    """The number of active knowledge bases reached the configured limit."""


class DefaultKnowledgeBaseError(ValueError):
    """The default knowledge base cannot be deleted until another is selected."""


@dataclass(frozen=True)
class KnowledgeBaseEntry:
    """Public fields of a knowledge base."""

    id: UUID
    name: str
    title: str
    description: str
    template: str
    created_by: UUID
    created_at: datetime
    is_default: bool = False


@dataclass(frozen=True)
class KnowledgeBaseDetail:
    """Catalog metadata and counts for one active knowledge base."""

    name: str
    title: str
    description: str
    template: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    source_counts: dict[str, int]
    chunk_counts: dict[str, dict[str, int]]


async def _detail(session: AsyncSession, row: KnowledgeBase) -> KnowledgeBaseDetail:
    """Read the creator username and aggregate active rows for a KB."""

    creator = (await session.execute(
        select(User.username).where(User.id == row.created_by)
    )).scalar_one()
    source_rows = await session.execute(
        select(KnowledgeSource.status, func.count()).where(
            KnowledgeSource.knowledge_base_id == row.id,
            KnowledgeSource.deleted_at.is_(None),
        ).group_by(KnowledgeSource.status)
    )
    kind = KnowledgeChunk.details["chunk_kind"].astext
    chunk_rows = await session.execute(
        select(kind, KnowledgeChunk.status, func.count()).where(
            KnowledgeChunk.knowledge_base_id == row.id,
            KnowledgeChunk.deleted_at.is_(None),
        ).group_by(kind, KnowledgeChunk.status)
    )
    source_counts = dict.fromkeys((status.value for status in SourceStatus), 0)
    source_counts.update(dict(source_rows.all()))
    chunk_counts = {
        kind: dict.fromkeys((status.value for status in ChunkStatus), 0)
        for kind in ("knowledge", "document_passage")
    }
    for kind_value, status, count in chunk_rows:
        if kind_value in chunk_counts:
            chunk_counts[kind_value][status] = count
    return KnowledgeBaseDetail(
        row.name, row.title, row.description or "", row.template, creator,
        row.created_at, row.updated_at, source_counts, chunk_counts,
    )


def _entry(row: KnowledgeBase) -> KnowledgeBaseEntry:
    return KnowledgeBaseEntry(
        id=row.id,
        name=row.name,
        title=row.title,
        description=row.description or "",
        template=row.template,
        created_by=row.created_by,
        created_at=row.created_at,
        is_default=row.is_default,
    )


class KnowledgeBaseRepository(Repository):
    """Lists, resolves and creates knowledge bases; soft-deleted ones are hidden."""

    @staticmethod
    def listener(database_url: str) -> PgListener:
        """Listen for committed changes to the application catalog."""

        url = make_url(database_url).set(drivername="postgresql")
        return PgListener(url.render_as_string(hide_password=False), [CATALOG_CHANNEL])

    @staticmethod
    async def _notify(session: AsyncSession, name: str) -> None:
        """Publish the changed name when the catalog transaction commits."""

        await session.execute(
            text("SELECT pg_notify(:channel, :name)"),
            {"channel": CATALOG_CHANNEL, "name": name},
        )

    async def list_active(self) -> list[KnowledgeBaseEntry]:
        """Return the knowledge bases that are not deleted, oldest first."""

        async with self._transaction() as session:
            rows = (
                await session.scalars(
                    select(KnowledgeBase)
                    .where(KnowledgeBase.deleted_at.is_(None))
                    .order_by(KnowledgeBase.created_at, KnowledgeBase.name)
                )
            ).all()
        return [_entry(row) for row in rows]

    async def get_by_name(self, name: str) -> KnowledgeBaseEntry | None:
        """Return the active knowledge base with this name, or None."""

        async with self._transaction() as session:
            row = await session.scalar(
                select(KnowledgeBase).where(
                    KnowledgeBase.name == name, KnowledgeBase.deleted_at.is_(None)
                )
            )
        return _entry(row) if row else None

    async def create(
        self,
        *,
        name: str,
        title: str,
        description: str,
        template: str,
        created_by: UUID,
        limit: int | None = None,
    ) -> KnowledgeBaseEntry:
        """Insert a knowledge base after checking the creation limit.

        Args:
            name: Validated stable name.
            title: Display name.
            description: Purpose of the collection; empty when not given.
            template: Built-in knowledge base whose policy the new one copies.
            created_by: Account that creates it.
            limit: Maximum number of active knowledge bases, or None for no limit.

        Returns:
            The created knowledge base.

        Raises:
            KnowledgeBaseNameTaken: The name is used by an active knowledge base.
            KnowledgeBaseLimitReached: `limit` active knowledge bases already exist.
            KnowledgeStorageUnavailable: The database could not be reached.
        """

        row = KnowledgeBase(
            name=name,
            title=title,
            description=description or None,
            template=template,
            created_by=created_by,
        )
        try:
            async with self._transaction() as session:
                if limit is not None:
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": _CREATE_LOCK_KEY},
                    )
                    active = await session.scalar(
                        select(func.count())
                        .select_from(KnowledgeBase)
                        .where(KnowledgeBase.deleted_at.is_(None))
                    )
                    if active >= limit:
                        raise KnowledgeBaseLimitReached(
                            f"At most {limit} knowledge bases can be created"
                        )
                session.add(row)
                await self._notify(session, name)
        except IntegrityError as exc:
            if violated_constraint(exc) == ACTIVE_NAME_UNIQUE_INDEX:
                raise KnowledgeBaseNameTaken(name) from exc
            raise
        return _entry(row)

    async def detail(self, name: str) -> KnowledgeBaseDetail | None:
        """Return counts and creator information for an active KB."""

        async with self._transaction() as session:
            row = await session.scalar(select(KnowledgeBase).where(
                KnowledgeBase.name == name, KnowledgeBase.deleted_at.is_(None),
            ))
            return await _detail(session, row) if row is not None else None

    async def rename(
        self, name: str, *, title: str | None, description: str | None,
    ) -> tuple[KnowledgeBaseEntry, KnowledgeBaseDetail] | None:
        """Update display fields while keeping the name and policy stable."""

        async with self._transaction() as session:
            row = await session.scalar(select(KnowledgeBase).where(
                KnowledgeBase.name == name, KnowledgeBase.deleted_at.is_(None),
            ).with_for_update())
            if row is None:
                return None
            if title is not None:
                row.title = title
            if description is not None:
                row.description = description or None
            await session.flush()
            await session.refresh(row)
            await self._notify(session, name)
            return _entry(row), await _detail(session, row)

    async def set_default(self, name: str) -> bool:
        """Select one active KB as the Library target atomically.

        Returns:
            True if the selected knowledge base exists; otherwise False.
        """
        async with self._transaction() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": _DEFAULT_LOCK_KEY})
            row = await session.scalar(select(KnowledgeBase).where(
                KnowledgeBase.name == name, KnowledgeBase.deleted_at.is_(None),
            ).with_for_update())
            if row is None:
                return False
            await session.execute(update(KnowledgeBase).where(
                KnowledgeBase.is_default.is_(True), KnowledgeBase.deleted_at.is_(None),
                KnowledgeBase.id != row.id,
            ).values(is_default=False))
            row.is_default = True
            await session.flush()
            await self._notify(session, name)
            return True

    async def delete(self, name: str) -> bool:
        """Soft-delete a KB and fail its queued jobs atomically."""

        async with self._transaction() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": _DEFAULT_LOCK_KEY})
            row = await session.scalar(select(KnowledgeBase).where(
                KnowledgeBase.name == name, KnowledgeBase.deleted_at.is_(None),
            ).with_for_update())
            if row is not None and row.is_default:
                raise DefaultKnowledgeBaseError("Choose another default before deleting this knowledge base")
            kb_id = await session.scalar(update(KnowledgeBase).where(
                KnowledgeBase.name == name, KnowledgeBase.deleted_at.is_(None),
            ).values(deleted_at=func.now()).returning(KnowledgeBase.id))
            if kb_id is None:
                return False
            await fail_queued(session, kb_id, KB_DELETED)
            await self._notify(session, name)
            return True
