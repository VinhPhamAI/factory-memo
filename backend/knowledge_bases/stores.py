"""Open the store a knowledge base's agent searches."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from backend.knowledge_bases.base import KnowledgeBaseSpec, Retrieval
from backend.src.agents.rag.store import DocumentStore
from backend.src.storage import KnowledgeStore
from backend.src.storage.chunk_retrieval import ChunkRetrieval
from backend.src.storage.embeddings import (
    build_embeddings_from_env,
    embedding_dim_from_env,
    embedding_model_from_env,
)
from backend.src.storage.knowledge_store import min_vector_similarity_from_env
from backend.src.storage.repositories import (
    ChunkRepository,
    ExtractionRunRepository,
    SourceRepository,
)


async def open_document_store(
    spec: KnowledgeBaseSpec, *, query_instruction: str | None = None, init_schema: bool = True
) -> DocumentStore:
    """Chunk table spec.document_table in the knowledge base's database."""

    return await DocumentStore.for_database(
        spec.database,
        query_instruction=(
            spec.embedding_query_instruction if query_instruction is None else query_instruction
        ),
        init_schema=init_schema,
        table=spec.document_table,
        text_search_config=spec.text_search_config,
    )


async def open_store(spec: KnowledgeBaseSpec) -> KnowledgeStore | DocumentStore:
    """KnowledgeStore for knowledge retrieval, DocumentStore for document retrieval."""

    if spec.retrieval is Retrieval.DOCUMENTS:
        return await open_document_store(spec)
    return await KnowledgeStore.for_kb(spec)


def open_application_store(
    knowledge_base_id: UUID,
    spec: KnowledgeBaseSpec,
    *,
    sessions: Any,
    chunks: ChunkRepository | None = None,
    sources: SourceRepository | None = None,
    embeddings: Any = None,
) -> ChunkRetrieval:
    """Search store over one knowledge base of the application database.

    Args:
        knowledge_base_id: Application database ID of the knowledge base.
        spec: Policy of the knowledge base, for its name and retrieval settings.
        sessions: Async session factory of the shared application engine.
        chunks: Chunk repository to reuse; created from `sessions` when omitted.
        sources: Source repository to reuse; created from `sessions` when omitted.
        embeddings: Query embedding client; built from `EMBEDDING_*` when omitted.

    Returns:
        A store the agent tools search; closing it leaves the shared engine open.

    Raises:
        ValueError: `EMBEDDING_DIM` or the similarity threshold is not valid.
    """

    return ChunkRetrieval(
        knowledge_base_id,
        spec.name,
        chunks or ChunkRepository(sessions),
        sources or SourceRepository(sessions),
        ExtractionRunRepository(sessions, knowledge_base_id),
        embeddings if embeddings is not None else build_embeddings_from_env(
            query_instruction=spec.embedding_query_instruction
        ),
        vector_size=embedding_dim_from_env(),
        embedding_model=embedding_model_from_env(),
        min_vector_similarity=(
            min_vector_similarity_from_env()
            if spec.min_vector_similarity is None
            else spec.min_vector_similarity
        ),
    )
