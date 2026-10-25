"""transaction_events — append-only audit trail of every change to a transaction

Substrate for history / undo / learning-from-corrections. Append-only; never updated or deleted.
txn_id has NO foreign key on purpose, so an event survives a hard-deleted transaction.

Revision ID: 027
Revises: 026
"""
import sqlalchemy as sa
from alembic import op

revision = "027"; down_revision = "026"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.create_table(
        "transaction_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"),
                  nullable=False, server_default="1"),
        sa.Column("txn_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("field", sa.String(), nullable=True),
        sa.Column("old_value", sa.String(), nullable=True),
        sa.Column("new_value", sa.String(), nullable=True),
        sa.Column("actor", sa.String(), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_transaction_events_household_id", "transaction_events", ["household_id"])
    op.create_index("ix_transaction_events_txn_id", "transaction_events", ["txn_id"])
    op.create_index("ix_transaction_events_created_at", "transaction_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_transaction_events_created_at", table_name="transaction_events")
    op.drop_index("ix_transaction_events_txn_id", table_name="transaction_events")
    op.drop_index("ix_transaction_events_household_id", table_name="transaction_events")
    op.drop_table("transaction_events")
