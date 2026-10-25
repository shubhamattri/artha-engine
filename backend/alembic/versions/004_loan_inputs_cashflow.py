"""loans -> input-only schema (formula-driven) + cashflow_lines

Revision ID: 004
Revises: 003
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rebuild loans with input-only columns (old seed was fake/derived data).
    op.drop_table("loans")
    op.create_table(
        "loans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("lender", sa.String(), nullable=False),
        sa.Column("holder", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("original_principal", sa.Float(), nullable=False),
        sa.Column("annual_rate_pct", sa.Float(), nullable=False),
        sa.Column("tenure_months", sa.Integer(), nullable=False),
        sa.Column("disbursement_date", sa.Date(), nullable=False),
        sa.Column("extra_principal", sa.Float(), nullable=False, server_default="0"),
        sa.Column("emi_override", sa.Float(), nullable=True),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("closed_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "cashflow_lines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("amount_inr", sa.Float(), nullable=False),
        sa.Column("holder", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("cashflow_lines")
    op.drop_table("loans")
    op.create_table(
        "loans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("lender", sa.String(), nullable=False),
        sa.Column("balance_inr", sa.Float(), nullable=False),
        sa.Column("rate_pct", sa.Float(), nullable=False),
        sa.Column("emi_inr", sa.Float(), nullable=False),
        sa.Column("months_remaining", sa.Integer(), nullable=False),
        sa.Column("holder", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
    )
