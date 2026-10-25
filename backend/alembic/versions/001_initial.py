"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # statements
    op.create_table(
        "statements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("bank", sa.String(), nullable=False),
        sa.Column("card_type", sa.String(), nullable=False),
        sa.Column("card_last4", sa.String(length=4), nullable=False),
        sa.Column("stmt_month", sa.String(length=7), nullable=False),
        sa.Column("parsed_at", sa.DateTime(), nullable=True),
        sa.Column("total_due", sa.Float(), nullable=True),
        sa.Column("txn_count", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bank", "card_type", "stmt_month", name="uq_statement"
        ),
    )

    # transactions
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("statement_id", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sa.String(length=40), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("direction", sa.String(length=6), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("confidence", sa.String(length=4), nullable=True),
        sa.Column("txn_type", sa.String(length=10), nullable=True),
        sa.Column("bank", sa.String(), nullable=False),
        sa.Column("card_type", sa.String(), nullable=False),
        sa.Column("card_last4", sa.String(length=4), nullable=False),
        sa.Column("stmt_month", sa.String(length=7), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["statement_id"], ["statements.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_transaction_fingerprint"),
    )
    op.create_index(
        "ix_transactions_stmt_month", "transactions", ["stmt_month"]
    )
    op.create_index("ix_transactions_bank", "transactions", ["bank"])
    op.create_index("ix_transactions_category", "transactions", ["category"])

    # loans
    op.create_table(
        "loans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("lender", sa.String(), nullable=False),
        sa.Column("balance_inr", sa.Float(), nullable=False),
        sa.Column("rate_pct", sa.Float(), nullable=False),
        sa.Column("emi_inr", sa.Float(), nullable=False),
        sa.Column("months_remaining", sa.Integer(), nullable=False),
        sa.Column("holder", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # budgets
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("monthly_limit_inr", sa.Float(), nullable=False),
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category", "month", name="uq_budget"),
    )


def downgrade() -> None:
    op.drop_table("budgets")
    op.drop_table("loans")
    op.drop_index("ix_transactions_category", table_name="transactions")
    op.drop_index("ix_transactions_bank", table_name="transactions")
    op.drop_index("ix_transactions_stmt_month", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("statements")
