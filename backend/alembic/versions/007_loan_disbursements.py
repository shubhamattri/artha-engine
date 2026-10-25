"""loan disbursement tranches (flexi loans disburse in parts)

Revision ID: 007
Revises: 006
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # JSON list of {amount, date} tranches. Total drawn = sum of these.
    op.add_column("loans", sa.Column("disbursements", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("loans", "disbursements")
