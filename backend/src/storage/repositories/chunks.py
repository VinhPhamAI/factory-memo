"""Knowledge chunks: extracted knowledge records and their source links.

Services keep `KnowledgeRecord` as their record model; this module maps it to
`knowledge_chunks` rows. Subject and claim become `title` and `content`; the
other record fields live in `details`, next to `chunk_kind`, which tells
knowledge records from document passages. `evidence_id` is the linked source.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Collection, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, Text, and_, cast, func, literal, or_, select, text
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.agents.rag.chunking import Chunk
from backend.src.agents.rag.store import (
    MAX_EF_SEARCH,
    ChunkHit,
    SearchMode,
    rrf_fuse,
)
from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage.knowledge_store import (
    DEFAULT_MIN_VECTOR_SIMILARITY,
    DEFAULT_SEARCH_K,
    LEXICAL_CONFIDENT_MIN_TERMS,
    LEXICAL_CONFIDENT_TERM_FRACTION,
    MAX_SEARCH_CANDIDATES,
    MAX_SEARCH_K,
    MIN_SEARCH_CANDIDATES,
    RRF_RANK_CONSTANT,
    SEARCH_CANDIDATE_MULTIPLIER,
    lexical_or_query,
)
from backend.src.storage.models import (
    KnowledgeLinkUpdate,
    KnowledgeRecord,
    KnowledgeSearchHit,
    KnowledgeStatus,
)
from backend.src.storage.orm import (
    File,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeChunkSource,
    KnowledgeSource,
    new_id,
)
from backend.src.storage.orm.base import TEXT_SEARCH_CONFIG
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


    async def search_knowledge(
        self,
        knowledge_base_id: UUID,
        query: str,
        query_embedding: Sequence[float],
        *,
        filters: dict[str, Any] | None = None,
        k: int = DEFAULT_SEARCH_K,
        min_vector_similarity: float = DEFAULT_MIN_VECTOR_SIMILARITY,
    ) -> list[KnowledgeSearchHit]:
        """Fuse vector and full-text ranks of searchable knowledge by reciprocal rank.

        The nearest vector candidates are always kept. A hit is `confident`
        when its cosine similarity reaches the threshold or it shares enough
        query terms; confident hits come first. Chunks without an embedding
        are found by the full-text ranking only.

        Args:
            knowledge_base_id: Knowledge base to search.
            query: Question text, used for the full-text ranking.
            query_embedding: Embedding of the query.
            filters: Knowledge filter grammar from `build_knowledge_filter`.
            k: Number of hits to return, capped at `MAX_SEARCH_K`.
            min_vector_similarity: Cosine similarity that makes a hit confident.

        Returns:
            Hits ordered by confidence, then fused score.

        Raises:
            ValueError: The filter is not valid.
        """

        result_k = max(1, min(k, MAX_SEARCH_K))
        candidate_k = min(
            MAX_SEARCH_CANDIDATES,
            max(MIN_SEARCH_CANDIDATES, result_k * SEARCH_CANDIDATE_MULTIPLIER),
        )
        conditions = _record_conditions(knowledge_base_id, filters)
        async with self._transaction() as session:
            dense = await _dense_candidates(session, conditions, query_embedding, candidate_k)
            lexical, terms = await _lexical_candidates(session, conditions, query, candidate_k)
            matched = await _matched_terms(session, query, [row_id for row_id, _ in lexical])
            rows = await _chunks_by_id(session, [*dense, *(row_id for row_id, _ in lexical)])
            evidence = await _first_sources(session, list(rows))
        vector_ranks = {row_id: rank for rank, row_id in enumerate(dense, start=1)}
        lexical_ranks = {row_id: rank for rank, (row_id, _) in enumerate(lexical, start=1)}
        lexical_scores = dict(lexical)
        max_distance = 1.0 - min_vector_similarity
        required = max(
            min(LEXICAL_CONFIDENT_MIN_TERMS, terms),
            math.ceil(LEXICAL_CONFIDENT_TERM_FRACTION * terms),
        )
        hits = []
        for row_id in rows:
            vector_rank, lexical_rank = vector_ranks.get(row_id), lexical_ranks.get(row_id)
            distance = dense.get(row_id)
            shared = matched.get(row_id)
            hits.append(KnowledgeSearchHit(
                record=chunk_record(rows[row_id], evidence.get(row_id)),
                hybrid_score=sum(
                    1.0 / (RRF_RANK_CONSTANT + rank)
                    for rank in (vector_rank, lexical_rank) if rank is not None
                ),
                vector_rank=vector_rank,
                lexical_rank=lexical_rank,
                vector_distance=distance,
                lexical_score=lexical_scores.get(row_id),
                lexical_matched_terms=shared,
                confident=(distance is not None and distance <= max_distance)
                or (shared is not None and shared >= required),
            ))
        hits.sort(key=_hit_order)
        return hits[:result_k]

    async def search_passages(
        self,
        knowledge_base_id: UUID,
        query: str,
        query_embedding: Sequence[float] | None,
        *,
        mode: SearchMode = SearchMode.HYBRID,
        k: int = 10,
    ) -> list[ChunkHit]:
        """Rank searchable document passages by vector, full-text or fused rank.

        Args:
            knowledge_base_id: Knowledge base to search.
            query: Question text, used by the keyword and hybrid modes.
            query_embedding: Embedding of the query; required unless `mode` is keyword.
            mode: Ranking to use; hybrid fuses the top `MIN_SEARCH_CANDIDATES` or
                `k` of both rankings by reciprocal rank.
            k: Number of passages to return.

        Returns:
            Passages best first, each with its review status in `metadata`.

        Raises:
            ValueError: `k` is not positive, or an embedding is needed and missing.
        """

        if k <= 0:
            raise ValueError("k must be positive")
        mode = SearchMode(mode)
        if mode is not SearchMode.KEYWORD and query_embedding is None:
            raise ValueError("A query embedding is required for vector search")
        conditions = _passage_conditions(knowledge_base_id)
        candidates = k if mode is not SearchMode.HYBRID else max(k, MIN_SEARCH_CANDIDATES)
        async with self._transaction() as session:
            dense: dict[UUID, float] = {}
            lexical: list[tuple[UUID, float]] = []
            if mode is not SearchMode.KEYWORD:
                dense = await _dense_candidates(session, conditions, query_embedding, candidates)
            if mode is not SearchMode.SEMANTIC:
                lexical, _terms = await _lexical_candidates(session, conditions, query, candidates)
            rows = await _chunks_by_id(session, [*dense, *(row_id for row_id, _ in lexical)])
        vector_hits = [
            _passage_hit(rows[row_id], 1.0 - distance, vector_rank=rank)
            for rank, (row_id, distance) in enumerate(dense.items(), start=1)
        ]
        lexical_hits = [
            _passage_hit(rows[row_id], score, lexical_rank=rank)
            for rank, (row_id, score) in enumerate(lexical, start=1)
        ]
        if mode is SearchMode.SEMANTIC:
            return vector_hits[:k]
        if mode is SearchMode.KEYWORD:
            return lexical_hits[:k]
        return rrf_fuse(vector_hits, lexical_hits)[:k]

    async def get_document(self, knowledge_base_id: UUID, doc_id: str) -> list[ChunkHit]:
        """Return a document's searchable passages in order; consecutive ones may overlap."""

        async with self._transaction() as session:
            rows = (
                await session.scalars(
                    select(KnowledgeChunk)
                    .where(
                        *_passage_conditions(knowledge_base_id),
                        KnowledgeChunk.details["doc_id"].astext == doc_id,
                    )
                    .order_by(
                        KnowledgeChunk.details["chunk_index"].astext.cast(Integer),
                        KnowledgeChunk.id,
                    )
                )
            ).all()
        return [_passage_hit(row, 0.0) for row in rows]

    async def update_scope(
        self,
        knowledge_base_id: UUID,
        records: Sequence[KnowledgeRecord],
        *,
        leading_keys: Sequence[str] = (),
        revision_keys: Sequence[str] = (),
    ) -> int:
        """Store the new context of records with its search text and embedding atomically.

        The search text and embedding include the context, so they change
        together. Of the revision only `revision_keys` are written; links and
        history recorded since the records were read are kept.

        Args:
            knowledge_base_id: Knowledge base of the records.
            records: Records with the new context and their new embeddings.
            leading_keys: Context keys indexed first in the search text.
            revision_keys: Revision keys to overwrite from each record.

        Returns:
            The number of chunks updated; unknown or deleted records are skipped.

        Raises:
            ValueError: A record has no embedding.
        """

        if any(record.embedding is None for record in records):
            raise ValueError("Every record needs a new embedding with its new context")
        updated = 0
        async with self._transaction() as session:
            for record in records:
                row = await session.scalar(
                    select(KnowledgeChunk)
                    .where(
                        KnowledgeChunk.id == record.id,
                        KnowledgeChunk.knowledge_base_id == knowledge_base_id,
                        KnowledgeChunk.deleted_at.is_(None),
                        KnowledgeChunk.details["chunk_kind"].astext == KNOWLEDGE_CHUNK_KIND,
                    )
                    .with_for_update()
                )
                if row is None:
                    continue
                details = dict(row.details or {})
                details["context"] = json.loads(json_text(record.context))
                revision = dict(details.get("revision") or {})
                revision.update({
                    key: json.loads(json_text(record.revision[key]))
                    for key in revision_keys if key in record.revision
                })
                details["revision"] = revision
                row.details = details
                row.search_text = record.lexical_text(leading_keys)
                row.embedding = list(record.embedding)
                updated += 1
        return updated


def _searchable_conditions(knowledge_base_id: UUID, kind: str) -> list[Any]:
    """Build the inclusion predicate of every search over one kind of chunk.

    A chunk is searchable when it and its knowledge base are not deleted, it is
    not deprecated, and at least one linked source and that source's file (if
    any) are not deleted.
    """

    live_source = (
        select(literal(1))
        .select_from(KnowledgeChunkSource)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunkSource.source_id)
        .outerjoin(File, File.id == KnowledgeSource.file_id)
        .where(
            KnowledgeChunkSource.chunk_id == KnowledgeChunk.id,
            KnowledgeSource.deleted_at.is_(None),
            File.deleted_at.is_(None),
        )
        .exists()
    )
    live_base = (
        select(literal(1))
        .where(
            KnowledgeBase.id == KnowledgeChunk.knowledge_base_id,
            KnowledgeBase.deleted_at.is_(None),
        )
        .exists()
    )
    return [
        KnowledgeChunk.knowledge_base_id == knowledge_base_id,
        KnowledgeChunk.deleted_at.is_(None),
        KnowledgeChunk.status != KnowledgeStatus.DEPRECATED.value,
        KnowledgeChunk.details["chunk_kind"].astext == kind,
        live_base,
        live_source,
    ]


def _passage_conditions(knowledge_base_id: UUID) -> list[Any]:
    """Build the inclusion predicate of document-passage search."""

    return _searchable_conditions(knowledge_base_id, DOCUMENT_PASSAGE_KIND)


def _record_conditions(
    knowledge_base_id: UUID, filters: dict[str, Any] | None
) -> list[Any]:
    """Build the knowledge inclusion predicate and optional filter grammar."""

    conditions = _searchable_conditions(knowledge_base_id, KNOWLEDGE_CHUNK_KIND)
    conditions.append(KnowledgeChunk.superseded_by.is_(None))
    if filters is not None:
        conditions.append(_filter_expression(filters))
    return conditions


async def _dense_candidates(
    session: AsyncSession,
    conditions: Sequence[Any],
    query_embedding: Sequence[float] | None,
    limit: int,
) -> dict[UUID, float]:
    """Return the nearest embedded chunks as `{id: cosine distance}`, nearest first."""

    distance = KnowledgeChunk.embedding.cosine_distance(list(query_embedding or ()))
    # An HNSW scan returns at most ef_search rows before the conditions apply.
    await session.execute(text(f"SET LOCAL hnsw.ef_search = {int(MAX_EF_SEARCH)}"))
    rows = await session.execute(
        select(KnowledgeChunk.id, distance)
        .where(*conditions, KnowledgeChunk.embedding.is_not(None))
        .order_by(distance, KnowledgeChunk.id)
        .limit(limit)
    )
    return {row_id: float(value) for row_id, value in rows}


async def _lexical_candidates(
    session: AsyncSession, conditions: Sequence[Any], query: str, limit: int
) -> tuple[list[tuple[UUID, float]], int]:
    """Return chunks sharing any query term, best first, and the query's term count."""

    lexical = lexical_or_query(query)
    config = cast(TEXT_SEARCH_CONFIG, REGCONFIG)
    terms = await session.scalar(
        select(func.cardinality(func.tsvector_to_array(func.to_tsvector(config, query))))
    )
    if not lexical:
        return [], int(terms or 0)
    parsed = func.websearch_to_tsquery(config, lexical)
    score = func.ts_rank_cd(KnowledgeChunk.search_vector, parsed)
    rows = await session.execute(
        select(KnowledgeChunk.id, score)
        .where(*conditions, KnowledgeChunk.search_vector.op("@@")(parsed))
        .order_by(score.desc(), KnowledgeChunk.id)
        .limit(limit)
    )
    return [(row_id, float(value)) for row_id, value in rows], int(terms or 0)


async def _matched_terms(
    session: AsyncSession, query: str, chunk_ids: Sequence[UUID]
) -> dict[UUID, int]:
    """Count the distinct query terms each chunk's search text contains."""

    if not chunk_ids:
        return {}
    rows = await session.execute(
        text(
            "SELECT id, cardinality(ARRAY("
            "SELECT unnest(tsvector_to_array(search_vector)) INTERSECT "
            "SELECT unnest(tsvector_to_array("
            f"to_tsvector('{TEXT_SEARCH_CONFIG}'::regconfig, :query)))"
            ")) FROM knowledge_chunks WHERE id = ANY(:ids)"
        ),
        {"query": query, "ids": list(chunk_ids)},
    )
    return {row_id: int(count) for row_id, count in rows}


async def _chunks_by_id(
    session: AsyncSession, chunk_ids: Sequence[UUID]
) -> dict[UUID, KnowledgeChunk]:
    """Load chunk rows by ID, keeping the order of first appearance."""

    wanted = list(dict.fromkeys(chunk_ids))
    if not wanted:
        return {}
    rows = {
        row.id: row
        for row in await session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.id.in_(wanted))
        )
    }
    return {chunk_id: rows[chunk_id] for chunk_id in wanted}


def _hit_order(hit: KnowledgeSearchHit) -> tuple[Any, ...]:
    """Sort key: confident first, then fused score, channels, best rank and ID."""

    big = 2**31 - 1
    return (
        not hit.confident,
        -hit.hybrid_score,
        -((hit.vector_rank is not None) + (hit.lexical_rank is not None)),
        min(hit.vector_rank or big, hit.lexical_rank or big),
        hit.lexical_rank or big,
        big if hit.vector_distance is None else hit.vector_distance,
        str(hit.record.id),
    )


def _passage_hit(
    row: KnowledgeChunk,
    score: float,
    *,
    vector_rank: int | None = None,
    lexical_rank: int | None = None,
) -> ChunkHit:
    """Map a document-passage chunk to a search hit."""

    details = dict(row.details or {})
    doc_id = str(details.get("doc_id") or row.id)
    index = int(details.get("chunk_index") or 0)
    for internal in ("chunk_kind", "doc_id", "chunk_index", "review_history"):
        details.pop(internal, None)
    return ChunkHit(
        chunk_id=str(row.id),
        doc_id=doc_id,
        chunk_index=index,
        title=row.title,
        text=row.content,
        score=score,
        vector_rank=vector_rank,
        lexical_rank=lexical_rank,
        metadata={**details, "review_status": row.status},
    )


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
