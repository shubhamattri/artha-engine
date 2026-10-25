"""Record where a monthly target came from.

A budget row could be something the household typed, something a category's default seeded, or
something backfilled across months that never had a budget. All three look identical once written, so
nothing can tell you whether a figure was a decision or an assumption, and nothing can undo a bulk
fill without also deleting what a person set by hand.

NULL means typed by a person, which keeps every existing row meaning exactly what it meant before.

Revision ID: 063
Revises: 062
"""

import sqlalchemy as sa
from alembic import op

revision = "063"
down_revision = "062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("budgets", sa.Column("source", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("budgets", "source")
