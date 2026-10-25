"""recurring/accruing investments — SIP, RD, EPF, PPF (computed, not fetched)

Revision ID: 009
Revises: 008
"""

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recurring_investments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("holder", sa.String(), nullable=True),
        sa.Column("opening_balance", sa.Float(), nullable=False, server_default="0"),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("monthly_contribution", sa.Float(), nullable=False, server_default="0"),
        sa.Column("employer_contribution", sa.Float(), nullable=False, server_default="0"),
        sa.Column("annual_rate_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("compounding", sa.String(), nullable=False, server_default="annual"),
        sa.Column("maturity_date", sa.Date(), nullable=True),
        sa.Column("contributions", sa.JSON(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("recurring_investments")
