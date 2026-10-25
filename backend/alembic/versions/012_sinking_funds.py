"""sinking funds — monthly set-asides for lumpy expenses (the partner's budget method)

Revision ID: 012
Revises: 011
"""

import sqlalchemy as sa
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sinking_funds",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("monthly_contribution", sa.Float(), nullable=False, server_default="0"),
        sa.Column("opening_balance", sa.Float(), nullable=False, server_default="0"),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("target_annual", sa.Float(), nullable=True),
        sa.Column("holder", sa.String(), nullable=True),
        sa.Column("withdrawals", sa.JSON(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("sinking_funds")
