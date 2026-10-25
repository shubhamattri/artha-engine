"""Track what each cycle's leftover was earmarked for, and what has been drawn against it.

"We saved money last cycle — what happened to it?" had no answer. The leftover was computed, shown
once, and then existed nowhere: the next cycle started fresh and the money merged into the bank
balance with no record of where it went.

Deliberately NOT an asset table. The rupees are already in a bank account and that balance is
already in net worth; recording surplus as an asset would count the same money twice. These rows are
a claim on money already held, so spending them moves cash into a loan or an investment without
changing net worth by anything except the interest avoided or earned.

Revision ID: 056
Revises: 055
"""

import sqlalchemy as sa
from alembic import op

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "surplus_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("cycle_end", sa.String(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_surplus_alloc_hh", "surplus_allocations", ["household_id"])

    op.create_table(
        "surplus_draws",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("allocation_id", sa.Integer(),
                  sa.ForeignKey("surplus_allocations.id"), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("loan_id", sa.Integer(), sa.ForeignKey("loans.id"), nullable=True),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("transactions.id"), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("drawn_on", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_surplus_draw_alloc", "surplus_draws", ["allocation_id"])


def downgrade() -> None:
    op.drop_index("ix_surplus_draw_alloc", table_name="surplus_draws")
    op.drop_table("surplus_draws")
    op.drop_index("ix_surplus_alloc_hh", table_name="surplus_allocations")
    op.drop_table("surplus_allocations")
