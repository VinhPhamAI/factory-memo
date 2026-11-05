"""Index active source file hashes.

Revision ID: 095bca4b489d
Revises: ed8de1c70a6b
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "095bca4b489d"
down_revision: str | Sequence[str] | None = "ed8de1c70a6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill uploaded hashes and keep one active source for each file per KB."""

    op.add_column("knowledge_sources", sa.Column("original_sha256", sa.Text(), nullable=True))
    op.execute("""
        UPDATE knowledge_sources AS source
        SET original_sha256 = files.sha256
        FROM files
        WHERE source.file_id = files.id AND source.kind IN ('document', 'image')
    """)
    # Prefer the source with the most current evidence. Keep original rows,
    # references, extraction runs and bytes for all historical sources.
    op.execute("""
        WITH ranked AS (
            SELECT source.id, row_number() OVER (
                PARTITION BY source.knowledge_base_id, source.original_sha256
                ORDER BY (
                    SELECT count(*)
                    FROM knowledge_chunk_sources AS link
                    JOIN knowledge_chunks AS chunk ON chunk.id = link.chunk_id
                    WHERE link.source_id = source.id
                        AND chunk.deleted_at IS NULL AND chunk.status != 'deprecated'
                ) DESC, source.created_at, source.id
            ) AS position
            FROM knowledge_sources AS source
            WHERE source.deleted_at IS NULL AND source.original_sha256 IS NOT NULL
        ), removed AS (
            UPDATE knowledge_sources SET deleted_at = now()
            WHERE id IN (SELECT id FROM ranked WHERE position > 1)
            RETURNING id, file_id
        ), cancelled AS (
            UPDATE ingestion_jobs
            SET status = 'failed', error = 'SOURCE_REMOVED', locked_by = NULL, finished_at = now()
            WHERE source_id IN (SELECT id FROM removed) AND status = 'queued'
        )
        UPDATE files SET deleted_at = now()
        WHERE id IN (SELECT file_id FROM removed)
            AND deleted_at IS NULL
            AND NOT EXISTS (
                SELECT 1 FROM knowledge_sources AS source
                WHERE source.file_id = files.id AND source.deleted_at IS NULL
                    AND source.id NOT IN (SELECT id FROM removed)
            )
    """)
    op.create_index(
        "ix_knowledge_sources_active_original",
        "knowledge_sources",
        ["knowledge_base_id", "original_sha256"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND original_sha256 IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove the upload hash schema while preserving source history."""

    op.drop_index("ix_knowledge_sources_active_original", table_name="knowledge_sources")
    op.drop_column("knowledge_sources", "original_sha256")
