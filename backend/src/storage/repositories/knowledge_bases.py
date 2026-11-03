"""Knowledge-base rows: the collections the API serves."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from backend.src.storage.orm import KnowledgeBase
from backend.src.storage.repositories.base import Repository, violated_constraint


NAME_UNIQUE_CONSTRAINT = "uq_knowledge_bases_name"
# Serializes the limit check and the insert of concurrent creations.
_CREATE_LOCK_KEY = "knowledge_bases:create"


class KnowledgeBaseNameTaken(ValueError):
    """Another knowledge base, possibly a deleted one, already uses the name."""


class KnowledgeBaseLimitReached(ValueError):
    """The number of active knowledge bases reached the configured limit."""


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


def _entry(row: KnowledgeBase) -> KnowledgeBaseEntry:
    return KnowledgeBaseEntry(
        id=row.id,
        name=row.name,
        title=row.title,
        description=row.description or "",
        template=row.template,
        created_by=row.created_by,
        created_at=row.created_at,
    )


class KnowledgeBaseRepository(Repository):
    """Lists, resolves and creates knowledge bases; soft-deleted ones are hidden."""

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
            KnowledgeBaseNameTaken: The name is used, including by a deleted one.
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
        except IntegrityError as exc:
            if violated_constraint(exc) == NAME_UNIQUE_CONSTRAINT:
                raise KnowledgeBaseNameTaken(name) from exc
            raise
        return _entry(row)
