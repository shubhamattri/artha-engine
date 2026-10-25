"""transactions.household_id — tenant key for per-household isolation

Migration 017 created households/members but never scoped the domain tables to them, so every
transaction was globally visible. This adds the tenant key on `transactions`, backfills all existing
rows to the first (only) household, and indexes it. The read path filters on it (see api/transactions
list endpoint) so a household can never read another's ledger. First slice of true multi-tenancy
(multi-household, milestone 1).

Revision ID: 024
Revises: 023
"""
import sqlalchemy as sa
from alembic import op

revision = "024"; down_revision = "023"; branch_labels = None; depends_on = None


def upgrade() -> None:
    # 1) add nullable so existing rows are accepted, 2) backfill to the first household,
    # 3) enforce NOT NULL, 4) index for the per-household filter.
    op.add_column("transactions", sa.Column("household_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE transactions SET household_id = "
        "(SELECT id FROM households ORDER BY id LIMIT 1) WHERE household_id IS NULL"
    )
    op.alter_column("transactions", "household_id", nullable=False, server_default="1")
    op.create_index("ix_transactions_household_id", "transactions", ["household_id"])
    op.create_foreign_key(
        "fk_transactions_household_id", "transactions", "households", ["household_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_transactions_household_id", "transactions", type_="foreignkey")
    op.drop_index("ix_transactions_household_id", table_name="transactions")
    op.drop_column("transactions", "household_id")
