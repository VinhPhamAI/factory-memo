"""Build embedding clients and validate stored vectors against configuration."""

from __future__ import annotations

import os
from collections.abc import Collection

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.storage.knowledge_store import StorageError


# Local OpenAI-compatible servers (e.g. text-embeddings-inference) ignore the key,
# but the OpenAI client refuses to start without one.
LOCAL_API_KEY_PLACEHOLDER = "not-needed"


class QueryInstructionEmbeddings(Embeddings):
    """Prefix queries with a task instruction; documents are embedded as-is.

    Instruction-tuned models such as harrier-oss-v1, Qwen3-Embedding, or
    e5-instruct expect ``Instruct: <task>\nQuery: <query>`` on the query side only.
    """

    def __init__(self, embeddings: Embeddings, instruction: str) -> None:
        self.embeddings = embeddings
        self.instruction = instruction

    def _query(self, text: str) -> str:
        return f"Instruct: {self.instruction}\nQuery: {text}"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embeddings.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embeddings.embed_query(self._query(text))

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self.embeddings.aembed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return await self.embeddings.aembed_query(self._query(text))


def embedding_dim_from_env() -> int:
    """Vector size of the `knowledge_items.embedding` column."""

    raw = os.getenv("EMBEDDING_DIM")
    if not raw:
        raise ValueError("Missing required embedding configuration: EMBEDDING_DIM")
    try:
        dim = int(raw)
    except ValueError as exc:
        raise ValueError("EMBEDDING_DIM must be a positive integer") from exc
    if dim <= 0:
        raise ValueError("EMBEDDING_DIM must be a positive integer")
    return dim


def embedding_model_from_env() -> str | None:
    """Model that embeds documents and queries, or None when not configured."""

    return os.getenv("EMBEDDING_MODEL") or None


def _positive_int_from_env(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def build_embeddings_from_env(*, query_instruction: str = "") -> Embeddings:
    """Build a client from shared infrastructure settings and a KB instruction.

    The instruction is supplied by the knowledge base, never by a global
    environment variable. Empty instructions leave queries unchanged.
    """

    model = os.getenv("EMBEDDING_MODEL")
    base_url = os.getenv("EMBEDDING_BASE_URL") or None
    api_key = os.getenv("EMBEDDING_API_KEY") or (
        LOCAL_API_KEY_PLACEHOLDER if base_url else None
    )
    missing = [
        name
        for name, value in (("EMBEDDING_API_KEY", api_key), ("EMBEDDING_MODEL", model))
        if not value
    ]
    if missing:
        raise ValueError(
            "Missing required embedding configuration: " + ", ".join(missing)
        )

    # Only send `dimensions` when asked: many OpenAI-compatible providers
    # reject the parameter, while OpenAI's text-embedding-3 models honour it.
    dimensions = (
        embedding_dim_from_env()
        if os.getenv("EMBEDDING_SEND_DIMENSIONS", "").lower() in {"1", "true", "yes"}
        else None
    )
    options = {}
    # Servers such as text-embeddings-inference cap inputs per request.
    batch_size = _positive_int_from_env("EMBEDDING_BATCH_SIZE")
    if batch_size is not None:
        options["chunk_size"] = batch_size
    embeddings = OpenAIEmbeddings(
        model=model,
        api_key=api_key,
        base_url=base_url,
        dimensions=dimensions,
        # Tiktoken pre-chunking only matches OpenAI's own tokenizer.
        check_embedding_ctx_length=False,
        **options,
    )
    instruction = query_instruction.strip()
    if instruction:
        return QueryInstructionEmbeddings(embeddings, instruction)
    return embeddings


class EmbeddingMismatch(StorageError):
    """Stored vectors do not match the configured embedding size or model."""


async def stored_embedding_dim(sessions: async_sessionmaker[AsyncSession]) -> int | None:
    """Read the vector size of `knowledge_chunks.embedding` from the catalog.

    Args:
        sessions: Factory of sessions on the application database.

    Returns:
        The size stored as the column type modifier, or None when the column
        has no fixed size.
    """

    async with sessions() as session:
        size = await session.scalar(
            text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = CAST('knowledge_chunks' AS regclass) "
                "AND attname = 'embedding'"
            )
        )
    return size if size is not None and size > 0 else None


def check_embedding_dimension(*, stored: int | None, configured: int) -> None:
    """Require the column size to equal `EMBEDDING_DIM`.

    Args:
        stored: Vector size of the database column.
        configured: Size from `EMBEDDING_DIM`.

    Raises:
        EmbeddingMismatch: The sizes differ or the column has no fixed size.
    """

    if stored == configured:
        return
    found = "no fixed size" if stored is None else f"{stored} dimensions"
    raise EmbeddingMismatch(
        f"knowledge_chunks.embedding has {found} but EMBEDDING_DIM is {configured}. "
        f"Set EMBEDDING_DIM to {stored or 'the column size'}, or re-embed the chunks "
        f"and migrate the column to {configured} dimensions before serving "
        "ingestion or search."
    )


async def verify_embedding_dimension(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Compare the database column size with `EMBEDDING_DIM`.

    Raises:
        EmbeddingMismatch: The sizes differ.
        ValueError: `EMBEDDING_DIM` is missing or invalid.
    """

    check_embedding_dimension(
        stored=await stored_embedding_dim(sessions), configured=embedding_dim_from_env()
    )


def check_embedding_model(
    knowledge_base: str, *, configured: str | None, recorded: Collection[str]
) -> None:
    """Require that a knowledge base was not embedded with another model.

    Args:
        knowledge_base: Name used in the message.
        configured: Model from `EMBEDDING_MODEL`; None skips the check.
        recorded: Models in the settings of the knowledge base's done runs.

    Raises:
        EmbeddingMismatch: A done run used a model other than the configured one.
    """

    if configured is None:
        return
    other = sorted(model for model in recorded if model != configured)
    if other:
        raise EmbeddingMismatch(
            f"Knowledge base {knowledge_base} was embedded with {', '.join(other)} but "
            f"EMBEDDING_MODEL is {configured}. Restore EMBEDDING_MODEL, or re-embed the "
            "knowledge base with the new model before searching or ingesting."
        )
