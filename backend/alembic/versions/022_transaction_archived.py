"""transactions.archived — soft delete (keep history, exclude from views/totals)

Deleting a transaction destroyed its history. Instead we ARCHIVE it: the row stays (auditable,
restorable) but is excluded from every total and list. Reclassifying to a non-spend category is the
preferred way to drop something from spend; archive is for genuine "remove this entry".

Revision ID: 022
Revises: 021
"""
import sqlalchemy as sa
from alembic import op

revision = "022"; down_revision = "021"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("archived", sa.Boolean(), nullable=False,
                                            server_default=sa.false()))
    op.create_index("ix_transactions_archived", "transactions", ["archived"])


def downgrade() -> None:
    op.drop_index("ix_transactions_archived", table_name="transactions")
    op.drop_column("transactions", "archived")
