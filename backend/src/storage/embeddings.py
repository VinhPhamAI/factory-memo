"""Embedding model construction for the knowledge base."""

from __future__ import annotations

import os

from langchain_openai import OpenAIEmbeddings


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


def build_embeddings_from_env() -> OpenAIEmbeddings:
    """Build the OpenAI-compatible embedding client configured by environment."""

    api_key = os.getenv("EMBEDDING_API_KEY")
    model = os.getenv("EMBEDDING_MODEL")
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
    return OpenAIEmbeddings(
        model=model,
        api_key=api_key,
        base_url=os.getenv("EMBEDDING_BASE_URL") or None,
        dimensions=dimensions,
        # Tiktoken pre-chunking only matches OpenAI's own tokenizer.
        check_embedding_ctx_length=False,
    )
