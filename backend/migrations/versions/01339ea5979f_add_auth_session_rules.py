"""Keep auth session record dates with the shared trigger.

Reuses `maintain_record_dates` from revision 3665f6870fa4: `created_at` never
changes and `updated_at` moves on each update. Autogenerate does not compare
triggers; change them in a new hand-written revision.

Revision ID: 01339ea5979f
Revises: f841068e1683
Create Date: 2026-11-03 15:52:10.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = '01339ea5979f'
down_revision: str | Sequence[str] | None = 'f841068e1683'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TRIGGER maintain_record_dates BEFORE UPDATE ON auth_sessions "
        "FOR EACH ROW EXECUTE FUNCTION maintain_record_dates()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER maintain_record_dates ON auth_sessions")
