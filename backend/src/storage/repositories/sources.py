"""Knowledge sources and the file records of their original bytes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB

from backend.src.storage.orm import (
    File,
    KnowledgeChunk,
    KnowledgeChunkSource,
    KnowledgeSource,
    SourceKind,
    SourceStatus,
    new_id,
)
from backend.src.storage.repositories.base import Repository


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
            KnowledgeStorageUnavailable: The database could not be reached;
                nothing was written.
        """

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
            title=title,
            extracted_text=extracted_text,
            status=SourceStatus.RECEIVED.value,
            source_metadata=dict(metadata or {}),
            submitted_by=submitted_by,
        )
        async with self._transaction() as session:
            if file_row is not None:
                session.add(file_row)
                await session.flush()
                source.file_id = file_row.id
            session.add(source)
        return _source_entry(source, file_row)

    async def get(self, knowledge_base_id: UUID, source_id: UUID) -> SourceEntry | None:
        """Return a visible source of the knowledge base, or None."""

        async with self._transaction() as session:
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
        self, knowledge_base_id: UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[SourceEntry], int]:
        """Return one page of visible sources, newest first, and the total count.

        Each source carries the number of its non-deleted chunks per status.
        """

        visible = (KnowledgeSource.knowledge_base_id == knowledge_base_id, *_VISIBLE)
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
        async with self._transaction() as session:
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
        return await self.get(knowledge_base_id, source_id)
