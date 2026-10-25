"""Record money actually arriving against a sale receivable.

A sale splits its proceeds two ways: cash already banked, and `asset_disposals.receivable_inr` for
the part still owed. Net worth counts the second as a Cash & Bank line, correctly. Nothing joined
the two, so when money landed the household had to lower the receivable in one screen and raise a
cash asset in another. Doing only the first deletes that money from net worth without a trace, and
the receivable is exactly the shape of the Rs 6,50,000 that once went missing.

This table is the ledger that makes the pair inseparable: an arrival writes a row here, lowers the
receivable and credits the cash asset inside ONE transaction, so a half-applied arrival cannot
exist. It also answers what arrived, when, and against which sale, which the balance alone never
could.

`asset_id` is nullable on purpose: the credited cash row may later be deleted or renamed by the
household, and losing the pointer must not erase the record that the money came in.

Revision ID: 066
Revises: 065
"""

import sqlalchemy as sa
from alembic import op

revision = "066"
down_revision = "065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "disposal_receipts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("disposal_id", sa.Integer(), sa.ForeignKey("asset_disposals.id"), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("amount_inr", sa.Float(), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id"), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_disposal_receipts_household_id", "disposal_receipts", ["household_id"])
    op.create_index("ix_disposal_receipts_disposal_id", "disposal_receipts", ["disposal_id"])


def downgrade() -> None:
    op.drop_index("ix_disposal_receipts_disposal_id", table_name="disposal_receipts")
    op.drop_index("ix_disposal_receipts_household_id", table_name="disposal_receipts")
    op.drop_table("disposal_receipts")
