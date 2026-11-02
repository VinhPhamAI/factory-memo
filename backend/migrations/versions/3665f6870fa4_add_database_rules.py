"""Add database rules that autogenerate cannot express.

Adds trigger functions and triggers for record dates, stale embeddings, fixed
knowledge base IDs, same-collection evidence links and permanent inquiry
events, plus the embedding HNSW index when the embedding size allows it.
Autogenerate does not compare these objects; change them in a new hand-written
revision.

Revision ID: 3665f6870fa4
Revises: 2e755e6df2ca
Create Date: 2026-11-02 23:43:50.373148
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

from backend.src.storage.embeddings import embedding_dim_from_env


revision: str = '3665f6870fa4'
down_revision: str | Sequence[str] | None = '2e755e6df2ca'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# pgvector HNSW indexes support at most 2000 dimensions for `vector`.
HNSW_MAX_DIM = 2000

RECORD_DATE_TABLES = (
    "users",
    "machines",
    "files",
    "knowledge_bases",
    "knowledge_sources",
    "knowledge_chunks",
    "conversations",
    "inquiries",
    "ingestion_jobs",
    "extraction_runs",
    "content_signatures",
    "knowledge_conflicts",
)

FUNCTIONS = {
    # created_at never changes; updated_at moves to now() unless the update
    # sets it explicitly (for example when importing historical records).
    "maintain_record_dates": """
        BEGIN
            IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION '%.created_at cannot be changed', TG_TABLE_NAME
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at THEN
                NEW.updated_at := now();
            END IF;
            RETURN NEW;
        END
    """,
    # An embedding of old search text must not be used for the new text.
    "clear_stale_chunk_embedding": """
        BEGIN
            IF NEW.search_text IS DISTINCT FROM OLD.search_text
               AND NEW.embedding IS NOT DISTINCT FROM OLD.embedding THEN
                NEW.embedding := NULL;
            END IF;
            RETURN NEW;
        END
    """,
    # Fixed collections make the same-collection check of evidence links stable.
    "keep_knowledge_base_id": """
        BEGIN
            IF NEW.knowledge_base_id IS DISTINCT FROM OLD.knowledge_base_id THEN
                RAISE EXCEPTION '%.knowledge_base_id cannot be changed', TG_TABLE_NAME
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
    """,
    "check_chunk_source_collection": """
        BEGIN
            IF (SELECT knowledge_base_id FROM knowledge_chunks WHERE id = NEW.chunk_id)
               IS DISTINCT FROM
               (SELECT knowledge_base_id FROM knowledge_sources WHERE id = NEW.source_id)
            THEN
                RAISE EXCEPTION
                    'knowledge chunk % and source % belong to different knowledge bases',
                    NEW.chunk_id, NEW.source_id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
    """,
    # Corrections are new events; earlier events stay unchanged.
    "keep_inquiry_events_permanent": """
        BEGIN
            RAISE EXCEPTION 'inquiry events cannot be changed or deleted'
                USING ERRCODE = 'check_violation';
        END
    """,
}

# (trigger name, table, timing/events, function)
TRIGGERS = (
    *(
        ("maintain_record_dates", table, "BEFORE UPDATE", "maintain_record_dates")
        for table in RECORD_DATE_TABLES
    ),
    (
        "clear_stale_embedding",
        "knowledge_chunks",
        "BEFORE UPDATE OF search_text",
        "clear_stale_chunk_embedding",
    ),
    (
        "keep_knowledge_base_id",
        "knowledge_sources",
        "BEFORE UPDATE OF knowledge_base_id",
        "keep_knowledge_base_id",
    ),
    (
        "keep_knowledge_base_id",
        "knowledge_chunks",
        "BEFORE UPDATE OF knowledge_base_id",
        "keep_knowledge_base_id",
    ),
    (
        "check_same_collection",
        "knowledge_chunk_sources",
        "BEFORE INSERT OR UPDATE",
        "check_chunk_source_collection",
    ),
    (
        "keep_permanent",
        "inquiry_events",
        "BEFORE UPDATE OR DELETE",
        "keep_inquiry_events_permanent",
    ),
)


def embedding_dim(table: str, column: str) -> int:
    """Read the embedding size the table revision created.

    Args:
        table: Table that holds the embedding column.
        column: Embedding column name.

    Returns:
        The `vector` size stored as the column type modifier; EMBEDDING_DIM
        when writing SQL without a connection.
    """

    if context.is_offline_mode():
        return embedding_dim_from_env()
    return op.get_bind().scalar(
        sa.text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = CAST(:table AS regclass) AND attname = :column"
        ),
        {"table": table, "column": column},
    )


def upgrade() -> None:
    for name, body in FUNCTIONS.items():
        op.execute(
            f"CREATE FUNCTION {name}() RETURNS trigger "
            f"LANGUAGE plpgsql AS $function${body}$function$"
        )
    for trigger, table, timing, function in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {trigger} {timing} ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
    # Larger embeddings need another index type (for example halfvec); search
    # then scans the table until one is added in a later revision.
    if embedding_dim("knowledge_chunks", "embedding") <= HNSW_MAX_DIM:
        op.create_index(
            op.f("ix_knowledge_chunks_embedding_hnsw"),
            "knowledge_chunks",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
    for trigger, table, _timing, _function in TRIGGERS:
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
    for name in FUNCTIONS:
        op.execute(f"DROP FUNCTION {name}()")
