"""transactions.occurred_at — the precise transaction timestamp (for Tier-1 dedup)

SMS/email carry a precise time that the code was discarding (keeping only the day). Storing it lets
the matcher merge two captures within minutes of each other with certainty. NULL for statements
(banks print only a date).

Revision ID: 028
Revises: 027
"""
import sqlalchemy as sa
from alembic import op

revision = "028"; down_revision = "027"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("occurred_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "occurred_at")
