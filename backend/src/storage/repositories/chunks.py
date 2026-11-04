"""Knowledge chunks: extracted knowledge records and their source links.

Services keep `KnowledgeRecord` as their record model; this module maps it to
`knowledge_chunks` rows. Subject and claim become `title` and `content`; the
other record fields live in `details`, next to `chunk_kind`, which tells
knowledge records from document passages. `evidence_id` is the linked source.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, Text, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.agents.rag.chunking import Chunk
from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage.knowledge_store import lexical_or_query
from backend.src.storage.models import KnowledgeLinkUpdate, KnowledgeRecord, KnowledgeStatus
from backend.src.storage.orm import KnowledgeChunk, KnowledgeChunkSource, new_id
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
_CONTEXT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


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

        return await self.add_records_and_updates(
            knowledge_base_id,
            source_id,
            records,
            leading_keys=leading_keys,
            references=references,
        )

    async def add_records_and_updates(
        self,
        knowledge_base_id: UUID,
        source_id: UUID,
        records: Sequence[KnowledgeRecord],
        *,
        updates: Sequence[KnowledgeLinkUpdate] = (),
        leading_keys: Sequence[str] = (),
        references: Sequence[dict[str, Any]] = (SOURCE_REFERENCE,),
    ) -> list[UUID]:
        """Insert records, source links and consolidation updates atomically.

        Args:
            knowledge_base_id: Knowledge base of every inserted or updated chunk.
            source_id: Evidence source linked to each inserted record.
            records: New extracted records, including their embeddings.
            updates: Link or supersession changes to existing records.
            leading_keys: Context keys indexed first in each record's search text.
            references: Evidence locations within the source; at least one.

        Returns:
            IDs of the inserted records in input order.

        Raises:
            ValueError: A record names a different evidence source.
            LookupError: A consolidation update names an unknown record.
        """

        if any(record.evidence_id not in (None, source_id) for record in records):
            raise ValueError("Every record must cite the source being stored")
        if not records and not updates:
            return []
        async with self._transaction() as session:
            if records:
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
            await self._apply_updates(session, knowledge_base_id, updates)
        return [record.id for record in records]

    async def add_passages(
        self,
        knowledge_base_id: UUID,
        source_id: UUID,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> list[UUID]:
        """Store embedded document passages and their passage references atomically."""

        if len(chunks) != len(embeddings):
            raise ValueError("Each document passage needs one embedding")
        ids = [new_id() for _ in chunks]
        async with self._transaction() as session:
            session.add_all(
                KnowledgeChunk(
                    id=chunk_id,
                    knowledge_base_id=knowledge_base_id,
                    title=chunk.title or "",
                    content=chunk.text,
                    details={
                        **dict(chunk.metadata),
                        "chunk_kind": DOCUMENT_PASSAGE_KIND,
                        "doc_id": chunk.doc_id,
                        "chunk_index": chunk.index,
                        "review_history": [],
                    },
                    search_text=chunk.search_text,
                    embedding=list(vector),
                    status=KnowledgeStatus.DRAFT.value,
                )
                for chunk_id, chunk, vector in zip(ids, chunks, embeddings, strict=True)
            )
            await session.flush()
            session.add_all(
                KnowledgeChunkSource(
                    chunk_id=chunk_id,
                    source_id=source_id,
                    references=[{"type": "passage", "chunk_index": chunk.index}],
                )
                for chunk_id, chunk in zip(ids, chunks, strict=True)
            )
        return ids

    async def apply_updates(
        self,
        knowledge_base_id: UUID,
        updates: Sequence[KnowledgeLinkUpdate],
    ) -> None:
        """Apply record links and supersessions without replacing prior history."""

        if not updates:
            return
        async with self._transaction() as session:
            await self._apply_updates(session, knowledge_base_id, updates)

    async def _apply_updates(
        self,
        session: AsyncSession,
        knowledge_base_id: UUID,
        updates: Sequence[KnowledgeLinkUpdate],
    ) -> None:
        """Apply link updates inside the caller's transaction."""

        if not updates:
            return
        ids = list(dict.fromkeys(update.record_id for update in updates))
        rows = (
            await session.scalars(
                select(KnowledgeChunk)
                .where(
                    KnowledgeChunk.knowledge_base_id == knowledge_base_id,
                    KnowledgeChunk.id.in_(ids),
                    KnowledgeChunk.deleted_at.is_(None),
                    KnowledgeChunk.details["chunk_kind"].astext == KNOWLEDGE_CHUNK_KIND,
                )
                .order_by(KnowledgeChunk.id)
                .with_for_update()
            )
        ).all()
        current = {row.id: row for row in rows}
        if missing := [str(record_id) for record_id in ids if record_id not in current]:
            raise LookupError(f"Cannot link unknown knowledge: {', '.join(missing)}")
        for update in updates:
            row = current[update.record_id]
            details = dict(row.details or {})
            for name in ("supported_by", "contradicted_by", "conflict_with"):
                existing = [UUID(value) for value in details.get(name, [])]
                removed = set(getattr(update, f"unlink_{name}", ()))
                values = dict.fromkeys([*existing, *getattr(update, name)])
                details[name] = [str(value) for value in values if value not in removed]
            if update.superseded_by is not None and row.superseded_by is None:
                row.superseded_by = update.superseded_by
                row.status = KnowledgeStatus.DEPRECATED.value
            if update.event is not None:
                revision = dict(details.get("revision") or {})
                revision["history"] = [
                    *revision.get("history", []),
                    json.loads(json_text(update.event)),
                ]
                details["revision"] = revision
            row.details = details

    async def get_records(
        self, knowledge_base_id: UUID, ids: Sequence[UUID], *, embeddings: bool = False
    ) -> list[KnowledgeRecord]:
        """Return non-deleted records by ID in the requested order, skipping unknown IDs.

        Deprecated and superseded records are returned, as citations need them.
        """

        if not ids:
            return []
        async with self._transaction() as session:
            query = select(KnowledgeChunk)
            if embeddings:
                query = query.add_columns(cast(KnowledgeChunk.embedding, Text))
            found = (
                await session.execute(
                    query.where(
                        KnowledgeChunk.knowledge_base_id == knowledge_base_id,
                        KnowledgeChunk.id.in_(list(ids)),
                        KnowledgeChunk.deleted_at.is_(None),
                        KnowledgeChunk.details["chunk_kind"].astext == KNOWLEDGE_CHUNK_KIND,
                    )
                )
            ).all()
            rows = [item[0] for item in found]
            evidence = await _first_sources(session, [row.id for row in rows])
        by_id = {}
        for item in found:
            row = item[0]
            record = chunk_record(row, evidence.get(row.id))
            if embeddings and item[1]:
                record.embedding = json.loads(item[1])
            by_id[row.id] = record
        return [by_id[record_id] for record_id in ids if record_id in by_id]

    async def records_for_source(
        self, knowledge_base_id: UUID, source_id: UUID, *, embeddings: bool = False
    ) -> list[KnowledgeRecord]:
        """Return the non-deleted records linked to a source, oldest first."""

        async with self._transaction() as session:
            query = select(KnowledgeChunk)
            if embeddings:
                query = query.add_columns(cast(KnowledgeChunk.embedding, Text))
            found = (
                await session.execute(
                    query
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
        records = []
        for item in found:
            record = chunk_record(item[0], source_id)
            if embeddings and item[1]:
                record.embedding = json.loads(item[1])
            records.append(record)
        return records

    async def has_results(
        self, knowledge_base_id: UUID, source_id: UUID, mode: str
    ) -> bool:
        """Return whether a source already has chunks of the requested extraction mode."""

        kind = KNOWLEDGE_CHUNK_KIND if mode == "knowledge" else DOCUMENT_PASSAGE_KIND
        async with self._transaction() as session:
            found = await session.scalar(
                select(KnowledgeChunk.id)
                .join(KnowledgeChunkSource, KnowledgeChunkSource.chunk_id == KnowledgeChunk.id)
                .where(
                    KnowledgeChunk.knowledge_base_id == knowledge_base_id,
                    KnowledgeChunkSource.source_id == source_id,
                    KnowledgeChunk.deleted_at.is_(None),
                    KnowledgeChunk.details["chunk_kind"].astext == kind,
                )
                .limit(1)
            )
        return found is not None

    async def similar_records(
        self,
        knowledge_base_id: UUID,
        record: KnowledgeRecord,
        *,
        k: int,
        types: Collection[KnowledgeType | str] = (),
        filters: dict[str, Any] | None = None,
        min_similarity: float = 0.0,
    ) -> list[tuple[KnowledgeRecord, float]]:
        """Return searchable knowledge nearest to an already embedded record."""

        if record.embedding is None:
            raise ValueError("The query record has no embedding")
        distance = KnowledgeChunk.embedding.cosine_distance(record.embedding)
        similarity = (1 - distance).label("similarity")
        conditions = _record_conditions(knowledge_base_id, filters)
        if types:
            conditions.append(
                KnowledgeChunk.details["type"].astext.in_(
                    [KnowledgeType(value).value for value in types]
                )
            )
        query = (
            select(KnowledgeChunk, similarity)
            .where(
                *conditions,
                KnowledgeChunk.id != record.id,
                KnowledgeChunk.embedding.is_not(None),
                similarity >= min_similarity,
            )
            .order_by(distance, KnowledgeChunk.id)
            .limit(max(1, min(k, 100)))
        )
        async with self._transaction() as session:
            rows = (await session.execute(query)).all()
            evidence = await _first_sources(session, [row.id for row, _score in rows])
        return [
            (chunk_record(row, evidence.get(row.id)), float(score)) for row, score in rows
        ]

    async def lexical_records(
        self,
        knowledge_base_id: UUID,
        query_text: str,
        *,
        k: int,
        types: Collection[KnowledgeType | str] = (),
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[KnowledgeRecord, float]]:
        """Return searchable knowledge sharing terms with a free-text query."""

        lexical = lexical_or_query(query_text)
        if not lexical:
            return []
        parsed = func.websearch_to_tsquery("simple", lexical)
        score = func.ts_rank_cd(KnowledgeChunk.search_vector, parsed).label("score")
        conditions = _record_conditions(knowledge_base_id, filters)
        if types:
            conditions.append(
                KnowledgeChunk.details["type"].astext.in_(
                    [KnowledgeType(value).value for value in types]
                )
            )
        query = (
            select(KnowledgeChunk, score)
            .where(*conditions, KnowledgeChunk.search_vector.op("@@")(parsed))
            .order_by(score.desc(), KnowledgeChunk.id)
            .limit(max(1, min(k, 100)))
        )
        async with self._transaction() as session:
            rows = (await session.execute(query)).all()
            evidence = await _first_sources(session, [row.id for row, _score in rows])
        return [
            (chunk_record(row, evidence.get(row.id)), float(value)) for row, value in rows
        ]

    async def passages_for_source(
        self, knowledge_base_id: UUID, source_id: UUID
    ) -> list[dict[str, Any]]:
        """Return document-passage chunks linked to one source, in passage order."""

        async with self._transaction() as session:
            rows = (
                await session.scalars(
                    select(KnowledgeChunk)
                    .join(
                        KnowledgeChunkSource,
                        KnowledgeChunkSource.chunk_id == KnowledgeChunk.id,
                    )
                    .where(
                        KnowledgeChunk.knowledge_base_id == knowledge_base_id,
                        KnowledgeChunkSource.source_id == source_id,
                        KnowledgeChunk.deleted_at.is_(None),
                        KnowledgeChunk.details["chunk_kind"].astext
                        == DOCUMENT_PASSAGE_KIND,
                    )
                    .order_by(
                        KnowledgeChunk.details["chunk_index"].astext.cast(Integer),
                        KnowledgeChunk.id,
                    )
                )
            ).all()
        return [
            {
                "id": str(row.id),
                "doc_id": str((row.details or {}).get("doc_id") or row.id),
                "index": int((row.details or {}).get("chunk_index") or 0),
                "text": row.content,
                "title": row.title,
                "status": row.status,
                "history": (row.details or {}).get("review_history") or [],
            }
            for row in rows
        ]

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

    async def review_passage(
        self,
        knowledge_base_id: UUID,
        source_id: UUID,
        chunk_id: UUID,
        status: KnowledgeStatus,
        *,
        reviewer_id: UUID,
        event: dict[str, Any],
    ) -> bool:
        """Review one document passage linked to the requested source."""

        status = KnowledgeStatus(status)
        async with self._transaction() as session:
            row = await session.scalar(
                select(KnowledgeChunk)
                .join(
                    KnowledgeChunkSource,
                    KnowledgeChunkSource.chunk_id == KnowledgeChunk.id,
                )
                .where(
                    KnowledgeChunk.id == chunk_id,
                    KnowledgeChunk.knowledge_base_id == knowledge_base_id,
                    KnowledgeChunkSource.source_id == source_id,
                    KnowledgeChunk.deleted_at.is_(None),
                    KnowledgeChunk.details["chunk_kind"].astext
                    == DOCUMENT_PASSAGE_KIND,
                )
                .with_for_update()
            )
            if row is None:
                return False
            details = dict(row.details or {})
            details["review_history"] = [
                *details.get("review_history", []),
                json.loads(json_text(event)),
            ]
            row.details = details
            row.status = status.value
            reviewed = status is not KnowledgeStatus.DRAFT
            row.reviewed_by = reviewer_id if reviewed else None
            row.reviewed_at = datetime.now(timezone.utc) if reviewed else None
        return True


def _record_conditions(
    knowledge_base_id: UUID, filters: dict[str, Any] | None
) -> list[Any]:
    """Build the shared searchable-record predicate and optional filter grammar."""

    conditions: list[Any] = [
        KnowledgeChunk.knowledge_base_id == knowledge_base_id,
        KnowledgeChunk.deleted_at.is_(None),
        KnowledgeChunk.status != KnowledgeStatus.DEPRECATED.value,
        KnowledgeChunk.superseded_by.is_(None),
        KnowledgeChunk.details["chunk_kind"].astext == KNOWLEDGE_CHUNK_KIND,
    ]
    if filters is not None:
        conditions.append(_filter_expression(filters))
    return conditions


def _filter_expression(node: Any) -> Any:
    """Compile the legacy knowledge-filter grammar to application-table expressions."""

    if not isinstance(node, dict) or len(node) != 1:
        raise ValueError("Knowledge filter nodes must contain exactly one key")
    key, value = next(iter(node.items()))
    if key in {"$and", "$or"}:
        if not isinstance(value, list) or not value:
            raise ValueError(f"{key} requires a non-empty list")
        expressions = [_filter_expression(item) for item in value]
        return and_(*expressions) if key == "$and" else or_(*expressions)

    column: Any
    if key == "subject":
        column = KnowledgeChunk.title
    elif key in {"type", "valid_from", "valid_to"}:
        column = KnowledgeChunk.details[key].astext
    elif key == "evidence_id":
        column = KnowledgeChunkSource.source_id
    elif key.startswith("context."):
        context_key = key.removeprefix("context.")
        if not _CONTEXT_KEY.fullmatch(context_key):
            raise ValueError(f"Invalid context key: {context_key!r}")
        column = KnowledgeChunk.details["context"][context_key].astext
    else:
        raise ValueError(f"Unsupported knowledge filter field: {key}")
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError(f"Filter for {key} must contain exactly one operator")
    operator, operand = next(iter(value.items()))
    if operator == "$exists":
        if not isinstance(operand, bool):
            raise ValueError("$exists requires a boolean")
        expression = column.is_not(None) if operand else column.is_(None)
    elif operator == "$eq":
        expression = column == operand
    elif operator == "$ne":
        expression = column.is_distinct_from(operand)
    elif operator == "$ilike":
        expression = column.ilike(operand, escape="\\")
    elif operator == "$lte":
        expression = column <= str(operand)
    elif operator == "$gte":
        expression = column >= str(operand)
    else:
        raise ValueError(f"Unsupported knowledge filter operator: {operator}")
    if key != "evidence_id":
        return expression
    return select(1).where(
        KnowledgeChunkSource.chunk_id == KnowledgeChunk.id, expression
    ).exists()


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
