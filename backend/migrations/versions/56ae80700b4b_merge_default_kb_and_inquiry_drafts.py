"""merge default knowledge base and inquiry drafts

Revision ID: 56ae80700b4b
Revises: 2d194b280219, 7c3f9a1b5e20
Create Date: 2026-11-06 15:30:00.000000
"""

from collections.abc import Sequence


revision: str = "56ae80700b4b"
down_revision: str | Sequence[str] | None = ("2d194b280219", "7c3f9a1b5e20")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the default-KB and inquiry-draft branches; no schema change."""


def downgrade() -> None:
    """Split the branches again; no schema change."""
