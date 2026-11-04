"""Knowledge sources and the file records of their original bytes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.storage.orm import (
    File,
    IngestionJob,
    JobStatus,
    KnowledgeChunk,
    KnowledgeChunkSource,
    KnowledgeSource,
    SourceKind,
    SourceStatus,
    UserRole,
    new_id,
)
from backend.src.storage.repositories.base import Repository, violated_constraint
from backend.src.storage.repositories.lifecycle import (
    SOURCE_REMOVED, InactiveKnowledgeBase, fail_queued, lock_kb, lock_source,
)

ACTIVE_ORIGINAL_INDEX = "ix_knowledge_sources_active_original"


class SourceRemovalForbidden(RuntimeError):
    """Only the submitter, an expert or an administrator may remove a source."""


class SourceJobRunning(RuntimeError):
    """The source has a running ingestion job."""


@dataclass(frozen=True)
class NewFile:
    """Metadata of original bytes already saved in the file store."""

    filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    sha256: str


@dataclass(frozen=True)
class FileEntry:
    """Public fields of a stored file."""

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    sha256: str
    created_at: datetime


@dataclass(frozen=True)
class SourceEntry:
    """Public fields of a knowledge source and its file, if it has one."""

    id: UUID
    knowledge_base_id: UUID
    kind: str
    title: str
    status: str
    extracted_text: str | None
    metadata: dict[str, Any]
    submitted_by: UUID
    created_at: datetime
    updated_at: datetime
    file: FileEntry | None = None
    # Chunks linked to the source per status; filled by `list_page` only.
    chunk_counts: dict[str, int] = field(default_factory=dict)
    removed_at: datetime | None = None


def _file_entry(row: File | None) -> FileEntry | None:
    if row is None:
        return None
    return FileEntry(
        id=row.id,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        storage_key=row.storage_key,
        sha256=row.sha256,
        created_at=row.created_at,
    )


def _source_entry(
    row: KnowledgeSource, file: File | None, chunk_counts: dict[str, int] | None = None
) -> SourceEntry:
    return SourceEntry(
        id=row.id,
        knowledge_base_id=row.knowledge_base_id,
        kind=row.kind,
        title=row.title,
        status=row.status,
        extracted_text=row.extracted_text,
        metadata=dict(row.source_metadata or {}),
        submitted_by=row.submitted_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        removed_at=row.deleted_at,
        file=_file_entry(file),
        chunk_counts=dict(chunk_counts or {}),
    )


# A source is visible while neither it nor its file is deleted.
_VISIBLE = (KnowledgeSource.deleted_at.is_(None), File.deleted_at.is_(None))


class SourceRepository(Repository):
    """Creates, reads and updates sources of knowledge bases."""

    async def create(
        self,
        knowledge_base_id: UUID,
        *,
        kind: SourceKind,
        title: str,
        submitted_by: UUID,
        file: NewFile | None = None,
        extracted_text: str | None = None,
        metadata: dict[str, Any] | None = None,
        source_id: UUID | None = None,
    ) -> SourceEntry:
        """Insert a received source and its file record in one transaction.

        Args:
            knowledge_base_id: Knowledge base that receives the source.
            kind: Source form; document and image sources need `file`.
            title: Name shown for the source, such as the file name.
            submitted_by: Account that submitted it.
            file: Metadata of bytes already saved in the file store.
            extracted_text: Text known at submission, such as a typed transcript.
            metadata: Submission details, such as speakers or recording settings.
            source_id: Pre-generated ID, when the file store key contains it.

        Returns:
            The stored source with its file.

        Raises:
            ValueError: A document or image source has no file.
            InactiveKnowledgeBase: The KB has been deleted or does not exist.
            KnowledgeStorageUnavailable: The database could not be reached;
                nothing was written.
        """

        async with self._transaction() as session:
            if not await lock_kb(session, knowledge_base_id):
                raise InactiveKnowledgeBase()
            return await self._create(
                session, knowledge_base_id, kind=kind, title=title, submitted_by=submitted_by,
                file=file, extracted_text=extracted_text, metadata=metadata, source_id=source_id,
            )

    async def _create(
        self, session: AsyncSession, knowledge_base_id: UUID, *, kind: SourceKind,
        title: str, submitted_by: UUID, file: NewFile | None = None,
        extracted_text: str | None = None, metadata: dict[str, Any] | None = None,
        source_id: UUID | None = None,
    ) -> SourceEntry:
        """Insert the source and original file within the caller's transaction."""

        if file is None and kind is not SourceKind.TEXT:
            raise ValueError(f"A {kind.value} source needs a file")
        file_row = None
        if file is not None:
            file_row = File(
                filename=file.filename,
                content_type=file.content_type,
                size_bytes=file.size_bytes,
                storage_key=file.storage_key,
                sha256=file.sha256,
                uploaded_by=submitted_by,
            )
        source = KnowledgeSource(
            id=source_id or new_id(),
            knowledge_base_id=knowledge_base_id,
            kind=kind.value,
            original_sha256=file.sha256 if kind is not SourceKind.TEXT and file else None,
            title=title,
            extracted_text=extracted_text,
            status=SourceStatus.RECEIVED.value,
            source_metadata=dict(metadata or {}),
            submitted_by=submitted_by,
        )
        if file_row is not None:
            session.add(file_row)
            await session.flush()
            source.file_id = file_row.id
        session.add(source)
        await session.flush()
        return _source_entry(source, file_row)

    async def create_upload(
        self, knowledge_base_id: UUID, *, file: NewFile, submitted_by: UUID, source_id: UUID,
    ) -> tuple[SourceEntry, bool]:
        """Store an upload or return its active duplicate and a true duplicate flag."""

        async with self._transaction() as session:
            if not await lock_kb(session, knowledge_base_id):
                raise InactiveKnowledgeBase()
            while True:
                try:
                    async with session.begin_nested():
                        source = await self._create(
                            session, knowledge_base_id, kind=SourceKind.DOCUMENT, title=file.filename,
                            submitted_by=submitted_by, file=file, source_id=source_id,
                        )
                    return source, False
                except IntegrityError as exc:
                    if violated_constraint(exc) != ACTIVE_ORIGINAL_INDEX:
                        raise
                existing = (await session.execute(
                    select(KnowledgeSource, File)
                    .join(File, File.id == KnowledgeSource.file_id)
                    .where(
                        KnowledgeSource.knowledge_base_id == knowledge_base_id,
                        KnowledgeSource.original_sha256 == file.sha256,
                        KnowledgeSource.deleted_at.is_(None),
                    ).with_for_update(of=KnowledgeSource)
                )).one_or_none()
                if existing is not None:
                    return _source_entry(*existing), True
                # Removal won the race after the unique-index check. Try the insert again.

    async def remove(
        self, knowledge_base_id: UUID, source_id: UUID, *, actor_id: UUID, role: str,
    ) -> bool:
        """Remove a visible source and cancel its queued work in one transaction.

        Raises:
            SourceRemovalForbidden: The actor is neither the submitter nor privileged.
            SourceJobRunning: A worker currently holds a job for the source.
        """

        async with self._transaction() as session:
            if not await lock_kb(session, knowledge_base_id):
                return False
            source = await lock_source(session, knowledge_base_id, source_id)
            if source is None:
                return False
            if source.submitted_by != actor_id and role not in {UserRole.EXPERT, UserRole.ADMIN}:
                raise SourceRemovalForbidden()
            if await session.scalar(select(IngestionJob.id).where(
                IngestionJob.source_id == source_id, IngestionJob.status == JobStatus.RUNNING,
            )):
                raise SourceJobRunning()
            source.deleted_at = func.now()
            await fail_queued(session, knowledge_base_id, SOURCE_REMOVED, source_id=source_id)
            if source.file_id is not None:
                # Lock shared files before counting references so concurrent removals
                # of the last two sources cannot both leave their file active.
                await session.scalar(select(File.id).where(File.id == source.file_id).with_for_update())
                active_ref = select(KnowledgeSource.id).where(
                    KnowledgeSource.file_id == source.file_id, KnowledgeSource.deleted_at.is_(None),
                ).exists()
                await session.execute(update(File).where(
                    File.id == source.file_id, File.deleted_at.is_(None), ~active_ref,
                ).values(deleted_at=func.now()))
            return True

    async def get(
        self, knowledge_base_id: UUID, source_id: UUID, *, session: AsyncSession | None = None,
    ) -> SourceEntry | None:
        """Return a visible source of the knowledge base, or None."""

        async with self._transaction(session) as session:
            row = (
                await session.execute(
                    select(KnowledgeSource, File)
                    .outerjoin(File, File.id == KnowledgeSource.file_id)
                    .where(
                        KnowledgeSource.id == source_id,
                        KnowledgeSource.knowledge_base_id == knowledge_base_id,
                        *_VISIBLE,
                    )
                )
            ).one_or_none()
        return _source_entry(row[0], row[1]) if row else None

    async def list_page(
        self, knowledge_base_id: UUID, *, limit: int = 50, offset: int = 0,
        include_removed: bool = False,
    ) -> tuple[list[SourceEntry], int]:
        """Return one page of visible sources, newest first, and the total count.

        Each source carries the number of its non-deleted chunks per status.
        """

        visible = [KnowledgeSource.knowledge_base_id == knowledge_base_id]
        if not include_removed:
            visible.extend(_VISIBLE)
        async with self._transaction() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(KnowledgeSource)
                .outerjoin(File, File.id == KnowledgeSource.file_id)
                .where(*visible)
            )
            rows = (
                await session.execute(
                    select(KnowledgeSource, File)
                    .outerjoin(File, File.id == KnowledgeSource.file_id)
                    .where(*visible)
                    .order_by(KnowledgeSource.created_at.desc(), KnowledgeSource.id.desc())
                    .limit(limit)
                    .offset(max(0, offset))
                )
            ).all()
            counts: dict[UUID, dict[str, int]] = {}
            if rows:
                counted = await session.execute(
                    select(
                        KnowledgeChunkSource.source_id,
                        KnowledgeChunk.status,
                        func.count(),
                    )
                    .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeChunkSource.chunk_id)
                    .where(
                        KnowledgeChunkSource.source_id.in_([row[0].id for row in rows]),
                        KnowledgeChunk.deleted_at.is_(None),
                    )
                    .group_by(KnowledgeChunkSource.source_id, KnowledgeChunk.status)
                )
                for source_id, status, count in counted:
                    counts.setdefault(source_id, {})[status] = count
        entries = [_source_entry(source, file, counts.get(source.id)) for source, file in rows]
        return entries, int(total or 0)

    async def update(
        self,
        knowledge_base_id: UUID,
        source_id: UUID,
        *,
        status: SourceStatus | None = None,
        extracted_text: str | None = None,
        metadata: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> SourceEntry | None:
        """Change the status, text or metadata of a visible source.

        Args:
            knowledge_base_id: Knowledge base of the source.
            source_id: Source to change.
            status: New processing status, when it changes.
            extracted_text: Converted text, when conversion produced it.
            metadata: Top-level metadata entries to add or replace; other
                entries, such as the original transcript details, are kept.

        Returns:
            The updated source, or None when it is unknown or deleted.
        """

        values: dict[str, Any] = {}
        if status is not None:
            values["status"] = SourceStatus(status).value
        if extracted_text is not None:
            values["extracted_text"] = extracted_text
        if metadata:
            # `||` merges top-level keys, so concurrent writers of other keys keep theirs.
            values["source_metadata"] = KnowledgeSource.source_metadata.op("||")(
                literal(metadata, JSONB)
            )
        async with self._transaction(session) as session:
            if values:
                changed = await session.scalar(
                    update(KnowledgeSource)
                    .where(
                        KnowledgeSource.id == source_id,
                        KnowledgeSource.knowledge_base_id == knowledge_base_id,
                        KnowledgeSource.deleted_at.is_(None),
                    )
                    .values(**values)
                    .returning(KnowledgeSource.id)
                )
                if changed is None:
                    return None
            return await self.get(knowledge_base_id, source_id, session=session)
