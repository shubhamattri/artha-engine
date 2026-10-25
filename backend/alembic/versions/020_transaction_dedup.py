"""transactions.dup_of_id — cross-source dedup link (points at the surviving "primary" row)

A NULL dup_of_id means the row is active (counts everywhere). A non-NULL value means this row
is a duplicate of transaction <dup_of_id> and is excluded from every total. Reversible by setting
it back to NULL — we never hard-delete during reconciliation.

Revision ID: 020
Revises: 019
"""
import sqlalchemy as sa
from alembic import op

revision = "020"; down_revision = "019"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("dup_of_id", sa.Integer(), nullable=True))
    op.create_index("ix_transactions_dup_of_id", "transactions", ["dup_of_id"])


def downgrade() -> None:
    op.drop_index("ix_transactions_dup_of_id", table_name="transactions")
    op.drop_column("transactions", "dup_of_id")
