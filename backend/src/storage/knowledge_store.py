"""PostgreSQL + pgvector knowledge base backed by langchain-postgres."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from langchain_core.embeddings import Embeddings
from langchain_postgres import PGEngine, PGVectorStore
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage.models import (
    ConversationStatus,
    KnowledgeRecord,
    RawConversation,
)


KNOWLEDGE_TABLE = "knowledge_items"
RAW_CONVERSATION_TABLE = "raw_conversations"
ID_COLUMN = "id"
CONTENT_COLUMN = "claim"
EMBEDDING_COLUMN = "embedding"
METADATA_COLUMNS = [
    "subject",
    "type",
    "context",
    "evidence_id",
    "stated_by",
    "created_at",
    "valid_from",
    "valid_to",
    "conflict_with",
]
# pgvector cannot build an HNSW index over wider `vector` columns.
HNSW_MAX_DIM = 2000
DEFAULT_SEARCH_K = 5
MAX_SEARCH_K = 20
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

_KNOWLEDGE_TYPES_SQL = ", ".join(f"'{kind.value}'" for kind in KnowledgeType)
_STATUSES_SQL = ", ".join(f"'{status.value}'" for status in ConversationStatus)


class StorageError(RuntimeError):
    """Raised when the knowledge base cannot complete a database operation."""


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _matches_or_unscoped(field: str, value: str) -> dict[str, Any]:
    # Knowledge that names no machine applies to every machine, so keep it.
    return {"$or": [{field: {"$eq": value}}, {field: {"$exists": False}}]}


def build_knowledge_filter(
    *,
    subject: str | None = None,
    knowledge_type: KnowledgeType | str | None = None,
    machine_id: str | None = None,
    machine_model: str | None = None,
    valid_on: date | None = None,
) -> dict[str, Any] | None:
    """Translate retrieval constraints into a PGVectorStore metadata filter."""

    conditions: list[dict[str, Any]] = []
    if subject and subject.strip():
        conditions.append({"subject": {"$ilike": f"%{_escape_like(subject.strip())}%"}})
    if knowledge_type:
        conditions.append({"type": {"$eq": KnowledgeType(knowledge_type).value}})
    if machine_id and machine_id.strip():
        conditions.append(_matches_or_unscoped("context.machine_id", machine_id.strip()))
    if machine_model and machine_model.strip():
        conditions.append(
            _matches_or_unscoped("context.machine_model", machine_model.strip())
        )
    if valid_on is not None:
        conditions.append(
            {"$or": [{"valid_from": {"$exists": False}}, {"valid_from": {"$lte": valid_on}}]}
        )
        conditions.append(
            {"$or": [{"valid_to": {"$exists": False}}, {"valid_to": {"$gte": valid_on}}]}
        )
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


class KnowledgeStore:
    """Knowledge records (with embeddings) and the raw conversations they cite."""

    def __init__(
        self,
        engine: AsyncEngine,
        embeddings: Embeddings,
        vector_size: int,
        *,
        schema: str = "public",
    ) -> None:
        if not _IDENTIFIER_RE.match(schema):
            raise ValueError(f"Invalid schema name: {schema!r}")
        self._engine = engine
        self._schema = schema
        self._pg_engine = PGEngine.from_engine(engine)
        self._embeddings = embeddings
        self._vector_size = vector_size
        self._vector_store: PGVectorStore | None = None
        self._vector_store_lock = asyncio.Lock()

    @classmethod
    def from_url(
        cls,
        database_url: str,
        embeddings: Embeddings,
        vector_size: int,
        *,
        schema: str = "public",
        **engine_kwargs: Any,
    ) -> "KnowledgeStore":
        """Create a store over a `postgresql+psycopg://` connection URL."""

        engine_kwargs.setdefault("pool_pre_ping", True)
        return cls(
            create_async_engine(database_url, **engine_kwargs),
            embeddings,
            vector_size,
            schema=schema,
        )

    @classmethod
    def from_env(cls, embeddings: Embeddings | None = None) -> "KnowledgeStore":
        """Create a store from DATABASE_URL and the EMBEDDING_* settings."""

        from backend.src.storage.embeddings import (
            build_embeddings_from_env,
            embedding_dim_from_env,
        )

        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("Missing required database configuration: DATABASE_URL")
        return cls.from_url(
            database_url,
            embeddings or build_embeddings_from_env(),
            embedding_dim_from_env(),
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    def _table(self, name: str) -> str:
        return f'"{self._schema}"."{name}"'

    async def aclose(self) -> None:
        await self._engine.dispose()

    @asynccontextmanager
    async def _errors(self, action: str) -> AsyncIterator[None]:
        try:
            yield
        except SQLAlchemyError as exc:
            raise StorageError(f"Knowledge store failed to {action}: {exc}") from exc

    # ------------------------------------------------------------------ schema

    async def init_schema(self) -> None:
        """Create the extension, tables, constraints, and indexes if missing."""

        dim = self._vector_size
        raw_table = self._table(RAW_CONVERSATION_TABLE)
        knowledge_table = self._table(KNOWLEDGE_TABLE)
        statements = [
            "CREATE EXTENSION IF NOT EXISTS vector",
            f"""
            CREATE TABLE IF NOT EXISTS {raw_table} (
                id UUID PRIMARY KEY,
                transcript TEXT NOT NULL,
                stated_by TEXT,
                metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ({_STATUSES_SQL})),
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            # Column names must match the PGVectorStore mapping in _vector().
            f"""
            CREATE TABLE IF NOT EXISTS {knowledge_table} (
                {ID_COLUMN} UUID PRIMARY KEY,
                {CONTENT_COLUMN} TEXT NOT NULL,
                {EMBEDDING_COLUMN} vector({dim}) NOT NULL,
                subject TEXT NOT NULL,
                type TEXT NOT NULL CHECK (type IN ({_KNOWLEDGE_TYPES_SQL})),
                context JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                evidence_id UUID REFERENCES {raw_table}(id),
                stated_by TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                valid_from DATE,
                valid_to DATE,
                conflict_with UUID[] NOT NULL DEFAULT '{{}}',
                CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to)
            )
            """,
            f"CREATE INDEX IF NOT EXISTS {KNOWLEDGE_TABLE}_evidence_id_idx "
            f"ON {knowledge_table} (evidence_id)",
            f"CREATE INDEX IF NOT EXISTS {KNOWLEDGE_TABLE}_machine_id_idx "
            f"ON {knowledge_table} ((context->>'machine_id'))",
            f"CREATE INDEX IF NOT EXISTS {KNOWLEDGE_TABLE}_machine_model_idx "
            f"ON {knowledge_table} ((context->>'machine_model'))",
        ]
        if dim <= HNSW_MAX_DIM:
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {KNOWLEDGE_TABLE}_embedding_hnsw_idx "
                f"ON {knowledge_table} USING hnsw ({EMBEDDING_COLUMN} vector_cosine_ops)"
            )

        async with self._errors("initialise the schema"):
            async with self._engine.begin() as conn:
                for statement in statements:
                    await conn.execute(text(statement))
                stored_dim = await conn.scalar(
                    text(
                        "SELECT atttypmod FROM pg_attribute "
                        "WHERE attrelid = CAST(:table AS regclass) AND attname = :column"
                    ),
                    {"table": knowledge_table, "column": EMBEDDING_COLUMN},
                )
        if stored_dim != dim:
            raise StorageError(
                f"{KNOWLEDGE_TABLE}.{EMBEDDING_COLUMN} is vector({stored_dim}) but "
                f"EMBEDDING_DIM is {dim}; re-embed into a new table or fix the setting"
            )

    async def _vector(self) -> PGVectorStore:
        if self._vector_store is not None:
            return self._vector_store
        async with self._vector_store_lock:
            if self._vector_store is None:
                async with self._errors("open the vector store"):
                    self._vector_store = await PGVectorStore.create(
                        self._pg_engine,
                        embedding_service=self._embeddings,
                        table_name=KNOWLEDGE_TABLE,
                        schema_name=self._schema,
                        id_column=ID_COLUMN,
                        content_column=CONTENT_COLUMN,
                        embedding_column=EMBEDDING_COLUMN,
                        metadata_columns=METADATA_COLUMNS,
                        metadata_json_column=None,
                    )
        return self._vector_store

    # ------------------------------------------------------- raw conversations

    async def add_conversation(
        self,
        transcript: str,
        *,
        stated_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RawConversation:
        """Store one transcript as pending evidence."""

        async with self._errors("store a conversation"):
            async with self._engine.begin() as conn:
                row = (
                    await conn.execute(
                        text(
                            f"INSERT INTO {self._table(RAW_CONVERSATION_TABLE)} "
                            "(id, transcript, stated_by, metadata) "
                            "VALUES (:id, :transcript, :stated_by, CAST(:metadata AS jsonb)) "
                            "RETURNING *"
                        ),
                        {
                            "id": uuid4(),
                            "transcript": transcript,
                            "stated_by": stated_by,
                            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
                        },
                    )
                ).mappings().one()
        return RawConversation.model_validate(dict(row))

    async def mark_conversation(
        self,
        conversation_id: UUID,
        status: ConversationStatus,
        *,
        error: str | None = None,
    ) -> None:
        async with self._errors("update a conversation"):
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        f"UPDATE {self._table(RAW_CONVERSATION_TABLE)} "
                        "SET status = :status, error = :error WHERE id = :id"
                    ),
                    {"id": conversation_id, "status": status.value, "error": error},
                )

    async def get_conversation(self, conversation_id: UUID) -> RawConversation | None:
        async with self._errors("read a conversation"):
            async with self._engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(f"SELECT * FROM {self._table(RAW_CONVERSATION_TABLE)} WHERE id = :id"),
                        {"id": conversation_id},
                    )
                ).mappings().one_or_none()
        return RawConversation.model_validate(dict(row)) if row else None

    # ------------------------------------------------------------- knowledge

    async def add_knowledge(self, records: Sequence[KnowledgeRecord]) -> list[UUID]:
        """Embed and store knowledge records; returns their ids."""

        if not records:
            return []
        missing = [record for record in records if record.embedding is None]
        if missing:
            vectors = await self._embeddings.aembed_documents(
                [record.embedding_text() for record in missing]
            )
            for record, vector in zip(missing, vectors, strict=True):
                record.embedding = list(vector)
        for record in records:
            if len(record.embedding or []) != self._vector_size:
                raise StorageError(
                    f"Embedding for knowledge {record.id} has "
                    f"{len(record.embedding or [])} dimensions, expected {self._vector_size}"
                )

        store = await self._vector()
        async with self._errors("store knowledge"):
            await store.aadd_embeddings(
                texts=[record.claim for record in records],
                embeddings=[record.embedding for record in records],
                metadatas=[record.to_metadata() for record in records],
                ids=[str(record.id) for record in records],
            )
        return [record.id for record in records]

    async def search_knowledge(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        k: int = DEFAULT_SEARCH_K,
    ) -> list[tuple[KnowledgeRecord, float]]:
        """Semantic search; returns records with cosine distance (lower is closer)."""

        store = await self._vector()
        async with self._errors("search knowledge"):
            hits = await store.asimilarity_search_with_score(
                query, k=max(1, min(k, MAX_SEARCH_K)), filter=filters
            )
        return [(KnowledgeRecord.from_document(doc), float(score)) for doc, score in hits]

    async def get_knowledge(self, ids: Sequence[UUID]) -> list[KnowledgeRecord]:
        """Fetch records by id, in the requested order, skipping unknown ids."""

        if not ids:
            return []
        store = await self._vector()
        async with self._errors("read knowledge"):
            documents = await store.aget_by_ids([str(i) for i in ids])
        by_id = {UUID(doc.id): KnowledgeRecord.from_document(doc) for doc in documents}
        return [by_id[i] for i in ids if i in by_id]
