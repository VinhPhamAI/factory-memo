"""Chunk search of one knowledge base with the evidence of each hit."""

from __future__ import annotations

import logging
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from uuid import UUID

from langchain_core.embeddings import Embeddings

from backend.knowledge_bases.base import KnowledgeBaseSpec
from backend.src.agents.rag.store import SearchMode
from backend.src.storage.chunk_retrieval import ChunkRetrieval
from backend.src.storage.embeddings import (
    EmbeddingUnavailable,
    build_embeddings_from_env,
    embedding_dim_from_env,
    embedding_model_from_env,
)
from backend.src.storage.knowledge_store import (
    DEFAULT_MIN_VECTOR_SIMILARITY,
    build_knowledge_filter,
    min_vector_similarity_from_env,
)
from backend.src.storage.repositories import (
    ChunkRepository,
    ChunkSearchHit,
    ExtractionRunRepository,
    SourceRepository,
)
from backend.src.storage.repositories.chunks import KNOWLEDGE_CHUNK_KIND

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkSearchResult:
    """Ranked chunks with their active evidence."""

    # Model that embedded the query; None when the search embedded nothing.
    embedding_model: str | None
    hits: list[ChunkSearchHit]


class ChunkSearchService:
    """Ranks the chunks of a knowledge base for a query through `ChunkRetrieval`."""

    def __init__(
        self,
        sessions: object,
        chunks: ChunkRepository,
        sources: SourceRepository,
        *,
        embeddings: Embeddings | None = None,
        embedding_model: str | None = None,
        vector_size: int | None = None,
    ) -> None:
        """Bind the service to the shared repositories and embedding settings.

        Args:
            sessions: Async session factory of the application database.
            chunks: Repository that ranks chunks.
            sources: Source repository for the retrieval store.
            embeddings: Query embedding client for every knowledge base; built
                from `EMBEDDING_*` on first use when omitted.
            embedding_model: Configured model, defaulting to `EMBEDDING_MODEL`.
            vector_size: Expected query embedding size, defaulting to `EMBEDDING_DIM`.
        """

        self._sessions = sessions
        self._chunks = chunks
        self._sources = sources
        self._embeddings = embeddings
        self._built: dict[str, Embeddings] = {}
        self._embedding_model = (
            embedding_model if embedding_model is not None else embedding_model_from_env()
        )
        self._vector_size = vector_size

    def _embeddings_for(self, spec: KnowledgeBaseSpec) -> Embeddings:
        """Return the query client of a knowledge base, built from the environment once.

        Raises:
            EmbeddingUnavailable: The environment does not configure a client.
        """

        if self._embeddings is not None:
            return self._embeddings
        instruction = spec.embedding_query_instruction or ""
        if instruction not in self._built:
            try:
                self._built[instruction] = build_embeddings_from_env(
                    query_instruction=instruction
                )
            except ValueError as exc:
                raise EmbeddingUnavailable("The embedding provider is not configured") from exc
        return self._built[instruction]

    async def search(
        self,
        knowledge_base_id: UUID,
        spec: KnowledgeBaseSpec,
        query: str,
        *,
        mode: SearchMode,
        kinds: Collection[str],
        statuses: Collection[str],
        context: Mapping[str, str],
        limit: int,
    ) -> ChunkSearchResult:
        """Rank a knowledge base's searchable chunks and attach their evidence.

        Args:
            knowledge_base_id: Active knowledge base to search.
            spec: Its policy, for its name and query instruction.
            query: Query text.
            mode: Vector, full-text or fused ranking.
            kinds: Chunk kinds to search.
            statuses: Review statuses to include; deprecated is never searchable.
            context: Context keys and values the chunks must match; chunks that do
                not name a key still match it.
            limit: Number of chunks to return.

        Returns:
            The ranked chunks and, per chunk, its active sources with locations.

        Raises:
            EmbeddingMismatch: The knowledge base was embedded with another model.
            EmbeddingUnavailable: A vector search could not embed the query.
            KnowledgeStorageUnavailable: The database could not be reached.
            ValueError: A context key or another argument is not valid.
        """

        started = time.perf_counter()
        keyword = mode is SearchMode.KEYWORD
        use_confidence = mode is SearchMode.HYBRID and KNOWLEDGE_CHUNK_KIND in kinds
        min_similarity = spec.min_vector_similarity
        if min_similarity is None:
            min_similarity = (
                min_vector_similarity_from_env() if use_confidence else DEFAULT_MIN_VECTOR_SIMILARITY
            )
        retrieval = ChunkRetrieval(
            knowledge_base_id,
            spec.name,
            self._chunks,
            self._sources,
            ExtractionRunRepository(self._sessions, knowledge_base_id),
            None if keyword else self._embeddings_for(spec),
            vector_size=self._vector_size or embedding_dim_from_env(),
            embedding_model=self._embedding_model,
            min_vector_similarity=min_similarity,
        )
        hits = await retrieval.search_chunks(
            query,
            mode=mode,
            kinds=kinds,
            statuses=statuses,
            filters=build_knowledge_filter(context=dict(context)),
            limit=limit,
        )
        # The query text is not logged: it may name equipment or people.
        logger.info(
            "chunk search kb=%s mode=%s results=%d latency_ms=%d",
            spec.name, mode.value, len(hits), (time.perf_counter() - started) * 1000,
        )
        return ChunkSearchResult(
            embedding_model=None if keyword else self._embedding_model,
            hits=hits,
        )
