"""Check the target database and enable pgvector.

Stops when the database holds the running service's job queue (an
`ingestion_jobs` table with a `kb` column): the application tables must go
into a database separate from DATABASE_URL until existing data is moved.

Revision ID: eda1239388ad
Revises:
Create Date: 2026-11-02 23:42:03.730589
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op


revision: str = 'eda1239388ad'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def holds_service_job_queue() -> bool:
    """Report whether the current schema has the service-created job table."""

    return bool(
        op.get_bind().scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'ingestion_jobs' AND column_name = 'kb')"
            )
        )
    )


def upgrade() -> None:
    if not context.is_offline_mode() and holds_service_job_queue():
        raise RuntimeError(
            "This database holds the running service's ingestion_jobs table; "
            "set APP_DATABASE_URL to a separate database"
        )
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # The extension may predate this revision or serve other tables, so it stays.
    pass
