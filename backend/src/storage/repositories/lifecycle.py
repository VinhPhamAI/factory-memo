"""Locks shared by ingestion and soft deletion."""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.storage.orm import IngestionJob, JobStatus, KnowledgeBase, KnowledgeSource

KB_DELETED = "KB_DELETED"
SOURCE_REMOVED = "SOURCE_REMOVED"


class InactiveKnowledgeBase(LookupError):
    """The knowledge base cannot accept new work."""


class InactiveSource(LookupError):
    """The source cannot accept new work."""


async def lock_kb(session: AsyncSession, kb_id: UUID) -> bool:
    """Hold a shared lock on an active KB until the transaction finishes."""

    return await session.scalar(
        select(KnowledgeBase.id).where(
            KnowledgeBase.id == kb_id, KnowledgeBase.deleted_at.is_(None),
        ).with_for_update(read=True)
    ) is not None


async def lock_source(
    session: AsyncSession, kb_id: UUID, source_id: UUID, *, skip_locked: bool = False,
) -> KnowledgeSource | None:
    """Lock an active source after acquiring its KB lock."""

    return await session.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.id == source_id, KnowledgeSource.knowledge_base_id == kb_id,
            KnowledgeSource.deleted_at.is_(None),
        ).with_for_update(skip_locked=skip_locked)
    )


async def fail_queued(
    session: AsyncSession, kb_id: UUID, error: str, *, source_id: UUID | None = None,
) -> None:
    """Fail queued jobs while the caller holds the KB or source write lock."""

    filters = [IngestionJob.knowledge_base_id == kb_id, IngestionJob.status == JobStatus.QUEUED]
    if source_id is not None:
        filters.append(IngestionJob.source_id == source_id)
    await session.execute(
        update(IngestionJob).where(*filters).values(
            status=JobStatus.FAILED, error=error, finished_at=func.now(), locked_by=None,
        )
    )
