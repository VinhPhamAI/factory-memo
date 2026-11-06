"""backfill default knowledge base

Revision ID: 2d194b280219
Revises: b290a9847a81
Create Date: 2026-11-06 11:55:36.109092
"""

from collections.abc import Sequence

from alembic import op


revision: str = '2d194b280219'
down_revision: str | Sequence[str] | None = 'b290a9847a81'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preserve the former hard-coded Library target when it exists."""
    op.execute("""
        UPDATE knowledge_bases SET is_default = true
        WHERE name = 'knowledge_base_seed1' AND deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM knowledge_bases WHERE is_default AND deleted_at IS NULL
          )
    """)


def downgrade() -> None:
    """Keep any administrator's current selection when reverting this backfill."""
