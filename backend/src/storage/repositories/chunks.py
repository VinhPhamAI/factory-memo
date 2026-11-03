"""Knowledge chunks: extracted knowledge records and their source links.

Services keep `KnowledgeRecord` as their record model; this module maps it to
`knowledge_chunks` rows. Subject and claim become `title` and `content`; the
other record fields live in `details`, next to `chunk_kind`, which tells
knowledge records from document passages. `evidence_id` is the linked source.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.storage.models import KnowledgeRecord, KnowledgeStatus
from backend.src.storage.orm import KnowledgeChunk, KnowledgeChunkSource
from backend.src.storage.repositories.base import Repository, json_text


KNOWLEDGE_CHUNK_KIND = "knowledge"
DOCUMENT_PASSAGE_KIND = "document_passage"
# Link reference of a record extracted from the whole source text.
SOURCE_REFERENCE = {"type": "source"}
# Record fields stored in `details`; the rest map to columns or links.
_DETAIL_FIELDS = (
    "type", "context", "stated_by", "valid_from", "valid_to", "conflict_with",
    "supported_by", "contradicted_by", "revision",
)


def chunk_row(
    knowledge_base_id: UUID, record: KnowledgeRecord, *, leading_keys: Sequence[str] = ()
) -> KnowledgeChunk:
    """Map a knowledge record to a new chunk row.

    Args:
        knowledge_base_id: Knowledge base of the record.
        record: Record to store; its embedding may be missing.
        leading_keys: Context keys indexed first in the search text.

    Returns:
        An unsaved chunk with JSON-safe details.
    """

    details = record.model_dump(mode="json", include=set(_DETAIL_FIELDS))
    return KnowledgeChunk(
        id=record.id,
        knowledge_base_id=knowledge_base_id,
        title=record.subject,
        content=record.claim,
        details={"chunk_kind": KNOWLEDGE_CHUNK_KIND, **details},
        search_text=record.lexical_text(leading_keys),
        embedding=record.embedding,
        status=KnowledgeStatus(record.status).value,
        superseded_by=record.superseded_by,
    )


def chunk_record(row: KnowledgeChunk, evidence_id: UUID | None) -> KnowledgeRecord:
    """Rebuild the knowledge record of a chunk row, without its embedding."""

    details = row.details or {}
    return KnowledgeRecord.model_validate({
        **{name: details[name] for name in _DETAIL_FIELDS if name in details},
        "id": row.id,
        "subject": row.title,
        "claim": row.content,
        "status": row.status,
        "superseded_by": row.superseded_by,
        "created_at": row.created_at,
        "evidence_id": evidence_id,
    })


class ChunkRepository(Repository):
    """Stores, reads and reviews the knowledge records of a knowledge base."""

    async def add_records(
        self,
        knowledge_base_id: UUID,
        source_id: UUID,
        records: Sequence[KnowledgeRecord],
        *,
        leading_keys: Sequence[str] = (),
        references: Sequence[dict[str, Any]] = (SOURCE_REFERENCE,),
    ) -> list[UUID]:
        """Insert records and their links to one source in one transaction.

        Args:
            knowledge_base_id: Knowledge base of the records and the source.
            source_id: Source the records were extracted from.
            records: Records to insert, with embeddings when available.
            leading_keys: Context keys indexed first in the search text.
            references: Evidence locations within the source; at least one.

        Returns:
            The IDs of the inserted records, in input order.

        Raises:
            sqlalchemy.exc.IntegrityError: The source belongs to another
                knowledge base, or a record ID already exists; nothing is written.
            KnowledgeStorageUnavailable: The database could not be reached.
        """

        if not records:
            return []
        async with self._transaction() as session:
            session.add_all(
                chunk_row(knowledge_base_id, record, leading_keys=leading_keys)
                for record in records
            )
            await session.flush()
            session.add_all(
                KnowledgeChunkSource(
                    chunk_id=record.id, source_id=source_id, references=list(references)
                )
                for record in records
            )
        return [record.id for record in records]

    async def get_records(
        self, knowledge_base_id: UUID, ids: Sequence[UUID]
    ) -> list[KnowledgeRecord]:
        """Return non-deleted records by ID in the requested order, skipping unknown IDs.

        Deprecated and superseded records are returned, as citations need them.
        """

        if not ids:
            return []
        async with self._transaction() as session:
            rows = (
                await session.scalars(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.knowledge_base_id == knowledge_base_id,
                        KnowledgeChunk.id.in_(list(ids)),
                        KnowledgeChunk.deleted_at.is_(None),
                        KnowledgeChunk.details["chunk_kind"].astext == KNOWLEDGE_CHUNK_KIND,
                    )
                )
            ).all()
            evidence = await _first_sources(session, [row.id for row in rows])
        by_id = {row.id: chunk_record(row, evidence.get(row.id)) for row in rows}
        return [by_id[record_id] for record_id in ids if record_id in by_id]

    async def records_for_source(
        self, knowledge_base_id: UUID, source_id: UUID
    ) -> list[KnowledgeRecord]:
        """Return the non-deleted records linked to a source, oldest first."""

        async with self._transaction() as session:
            rows = (
                await session.scalars(
                    select(KnowledgeChunk)
                    .join(KnowledgeChunkSource, KnowledgeChunkSource.chunk_id == KnowledgeChunk.id)
                    .where(
                        KnowledgeChunk.knowledge_base_id == knowledge_base_id,
                        KnowledgeChunkSource.source_id == source_id,
                        KnowledgeChunk.deleted_at.is_(None),
                        KnowledgeChunk.details["chunk_kind"].astext == KNOWLEDGE_CHUNK_KIND,
                    )
                    .order_by(KnowledgeChunk.created_at, KnowledgeChunk.id)
                )
            ).all()
        return [chunk_record(row, source_id) for row in rows]

    async def review_record(
        self,
        knowledge_base_id: UUID,
        record_id: UUID,
        status: KnowledgeStatus,
        *,
        reviewer_id: UUID,
        event: dict[str, Any],
    ) -> KnowledgeRecord | None:
        """Store a reviewer's decision and append it to `revision["history"]`.

        Content is never edited. Approving or deprecating records the reviewer
        and time; returning a record to draft clears both, as the table
        requires them together.

        Args:
            knowledge_base_id: Knowledge base of the record.
            record_id: Record to review.
            status: New review status.
            reviewer_id: Signed-in account that made the decision.
            event: History entry explaining the decision.

        Returns:
            The updated record, or None when it is unknown, deleted or
            superseded (a superseded record stays deprecated).
        """

        status = KnowledgeStatus(status)
        async with self._transaction() as session:
            row = await session.scalar(
                select(KnowledgeChunk)
                .where(
                    KnowledgeChunk.id == record_id,
                    KnowledgeChunk.knowledge_base_id == knowledge_base_id,
                    KnowledgeChunk.deleted_at.is_(None),
                    KnowledgeChunk.superseded_by.is_(None),
                    KnowledgeChunk.details["chunk_kind"].astext == KNOWLEDGE_CHUNK_KIND,
                )
                .with_for_update()
            )
            if row is None:
                return None
            details = dict(row.details or {})
            revision = dict(details.get("revision") or {})
            revision["history"] = [*revision.get("history", []), json.loads(json_text(event))]
            details["revision"] = revision
            row.details = details
            row.status = status.value
            reviewed = status is not KnowledgeStatus.DRAFT
            row.reviewed_by = reviewer_id if reviewed else None
            row.reviewed_at = datetime.now(timezone.utc) if reviewed else None
            evidence = await _first_sources(session, [row.id])
        return chunk_record(row, evidence.get(row.id))


async def _first_sources(session: AsyncSession, chunk_ids: Sequence[UUID]) -> dict[UUID, UUID]:
    """Map each chunk to its earliest linked source (UUIDv7 order)."""

    if not chunk_ids:
        return {}
    links = await session.execute(
        select(KnowledgeChunkSource.chunk_id, KnowledgeChunkSource.source_id)
        .where(KnowledgeChunkSource.chunk_id.in_(list(chunk_ids)))
        .order_by(KnowledgeChunkSource.source_id)
    )
    first: dict[UUID, UUID] = {}
    for chunk_id, source_id in links:
        first.setdefault(chunk_id, source_id)
    return first
