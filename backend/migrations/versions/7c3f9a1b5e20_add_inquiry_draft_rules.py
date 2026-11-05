"""Allow draft inquiries without an assignee.

Revision ID: 7c3f9a1b5e20
Revises: da8178b423f6
"""

from collections.abc import Sequence

from alembic import op


revision: str = "7c3f9a1b5e20"
down_revision: str | Sequence[str] | None = "da8178b423f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# op.f() keeps the final names; the naming convention would prefix them again.
STATUS_CHECK = op.f("ck_inquiries_status_allowed")
ASSIGNEE_CHECK = op.f("ck_inquiries_draft_or_assigned")


def upgrade() -> None:
    """Add `draft` to the allowed statuses; only a draft may lack an assignee."""

    op.drop_constraint(STATUS_CHECK, "inquiries", type_="check")
    op.create_check_constraint(
        STATUS_CHECK,
        "inquiries",
        "status IN ('draft', 'open', 'in_review', 'needs_info', 'answered', 'closed')",
    )
    op.create_check_constraint(
        ASSIGNEE_CHECK, "inquiries", "status = 'draft' OR assigned_to IS NOT NULL"
    )


def downgrade() -> None:
    """Restore the previous checks; fails while draft inquiries still exist.

    Delete or submit every draft before downgrading: the older schema has no
    `draft` status and requires an assignee.
    """

    op.drop_constraint(ASSIGNEE_CHECK, "inquiries", type_="check")
    op.drop_constraint(STATUS_CHECK, "inquiries", type_="check")
    op.create_check_constraint(
        STATUS_CHECK,
        "inquiries",
        "status IN ('open', 'in_review', 'needs_info', 'answered', 'closed')",
    )
