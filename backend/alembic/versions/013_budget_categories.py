"""zero-based budget plan — variable-spend category targets (weekly/monthly layers)

Revision ID: 013
Revises: 012
"""

import sqlalchemy as sa
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_categories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("layer", sa.String(), nullable=False, server_default="monthly"),
        sa.Column("monthly_target", sa.Float(), nullable=False, server_default="0"),
        sa.Column("match_categories", sa.JSON(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("holder", sa.String(), nullable=True, server_default="Household"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("budget_categories")
