"""Agent search store backed by the application database's knowledge chunks.

`ChunkRetrieval` has the interface of `KnowledgeStore` and `DocumentStore`
that the agent tools use, so tools, citation checks and stream events work
unchanged. It answers from one knowledge base's `knowledge_chunks`; the
repositories define which chunks are searchable.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from langchain_core.embeddings import Embeddings

from backend.src.agents.rag.store import (
    DOCUMENT_OVERFETCH,
    ChunkHit,
    SearchMode,
    best_chunk_per_document,
)
from backend.src.storage.embeddings import check_embedding_model
from backend.src.storage.knowledge_store import DEFAULT_MIN_VECTOR_SIMILARITY, DEFAULT_SEARCH_K, StorageError
from backend.src.storage.models import (
    ConversationStatus,
    KnowledgeRecord,
    KnowledgeSearchHit,
    RawConversation,
)
from backend.src.storage.repositories import (
    ChunkRepository,
    ExtractionRunRepository,
    SourceRepository,
)


class ChunkRetrieval:
    """Searches the knowledge and document passages of one knowledge base."""

    def __init__(
        self,
        knowledge_base_id: UUID,
        knowledge_base_name: str,
        chunks: ChunkRepository,
        sources: SourceRepository,
        runs: ExtractionRunRepository,
        embeddings: Embeddings,
        *,
        vector_size: int,
        embedding_model: str | None = None,
        min_vector_similarity: float = DEFAULT_MIN_VECTOR_SIMILARITY,
    ) -> None:
        """Bind the store to a knowledge base and its embedding configuration.

        Args:
            knowledge_base_id: Knowledge base every search is limited to.
            knowledge_base_name: Name used in error messages.
            chunks: Repository that runs the searches.
            sources: Repository that reads source text for evidence.
            runs: Extraction runs of the knowledge base, for the embedding model check.
            embeddings: Client that embeds queries.
            vector_size: Expected size of every query embedding.
            embedding_model: Configured `EMBEDDING_MODEL`; None skips the model check.
            min_vector_similarity: Cosine similarity that makes a knowledge hit confident.
        """

        self._knowledge_base_id = knowledge_base_id
        self._name = knowledge_base_name
        self._chunks = chunks
        self._sources = sources
        self._runs = runs
        self._embeddings = embeddings
        self._vector_size = vector_size
        self._embedding_model = embedding_model
        self._min_vector_similarity = min_vector_similarity

    async def aclose(self) -> None:
        """Nothing to release: the engine belongs to the application."""

    async def _query_embedding(self, query: str) -> list[float]:
        """Embed a query after checking the knowledge base's embedding model.

        Raises:
            EmbeddingMismatch: The knowledge base was embedded with another model.
            StorageError: The embedding has an unexpected size.
        """

        check_embedding_model(
            self._name,
            configured=self._embedding_model,
            recorded=await self._runs.embedding_models(),
        )
        vector = list(await self._embeddings.aembed_query(query))
        if len(vector) != self._vector_size:
            raise StorageError(
                f"Query embedding has {len(vector)} dimensions, expected {self._vector_size}"
            )
        return vector

    async def search_knowledge(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        k: int = DEFAULT_SEARCH_K,
        min_vector_similarity: float | None = None,
    ) -> list[KnowledgeSearchHit]:
        """Fuse vector and full-text ranks of searchable knowledge; see `ChunkRepository`."""

        return await self._chunks.search_knowledge(
            self._knowledge_base_id,
            query,
            await self._query_embedding(query),
            filters=filters,
            k=k,
            min_vector_similarity=(
                self._min_vector_similarity
                if min_vector_similarity is None
                else min_vector_similarity
            ),
        )

    async def get_knowledge(self, ids: Sequence[UUID]) -> list[KnowledgeRecord]:
        """Return knowledge records by ID, skipping unknown and deleted ones."""

        return await self._chunks.get_records(self._knowledge_base_id, ids)

    async def get_conversation(self, conversation_id: UUID) -> RawConversation | None:
        """Return a live source's text as the evidence a knowledge record cites."""

        source = await self._sources.get(self._knowledge_base_id, conversation_id)
        if source is None:
            return None
        return RawConversation(
            id=source.id,
            transcript=source.extracted_text or "",
            metadata=source.metadata,
            status=ConversationStatus.PROCESSED,
            created_at=source.created_at,
        )

    async def search_documents(
        self,
        query: str,
        *,
        mode: SearchMode = SearchMode.HYBRID,
        k: int = 10,
        query_embedding: Sequence[float] | None = None,
    ) -> list[ChunkHit]:
        """Return the top documents, each represented by its best passage (MaxP)."""

        mode = SearchMode(mode)
        if k <= 0:
            raise ValueError("k must be positive")
        if mode is not SearchMode.KEYWORD and query_embedding is None:
            query_embedding = await self._query_embedding(query)
        hits = await self._chunks.search_passages(
            self._knowledge_base_id,
            query,
            query_embedding,
            mode=mode,
            k=k * DOCUMENT_OVERFETCH,
        )
        return best_chunk_per_document(hits)[:k]

    async def get_document(self, doc_id: str) -> list[ChunkHit]:
        """Return a document's live passages in order."""

        return await self._chunks.get_document(self._knowledge_base_id, doc_id)
