"""Track source removal in extraction runs.

Revision ID: 5e14ab829ad7
Revises: b1cd15d8d7de
"""

from collections.abc import Sequence

from alembic import op


revision: str = "5e14ab829ad7"
down_revision: str | Sequence[str] | None = "b1cd15d8d7de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep the duplicate index in sync with source removal and backfill old rows."""

    op.execute("""
        CREATE FUNCTION sync_extraction_run_source_removal() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE extraction_runs
            SET source_removed = (NEW.deleted_at IS NOT NULL)
            WHERE source_id = NEW.id AND knowledge_base_id = NEW.knowledge_base_id;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER sync_extraction_run_source_removal
        AFTER UPDATE OF deleted_at ON knowledge_sources
        FOR EACH ROW
        WHEN ((OLD.deleted_at IS NULL) <> (NEW.deleted_at IS NULL))
        EXECUTE FUNCTION sync_extraction_run_source_removal()
    """)
    op.execute("""
        UPDATE extraction_runs AS run
        SET source_removed = true
        FROM knowledge_sources AS source
        WHERE run.source_id = source.id AND run.knowledge_base_id = source.knowledge_base_id
            AND source.deleted_at IS NOT NULL AND NOT run.source_removed
    """)


def downgrade() -> None:
    """Remove source-removal synchronization."""

    op.execute("DROP TRIGGER sync_extraction_run_source_removal ON knowledge_sources")
    op.execute("DROP FUNCTION sync_extraction_run_source_removal()")
