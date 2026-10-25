"""transactions.bank_ref — the bank's own transaction reference (UPI RRN / NEFT-IMPS UTR)

The globally-unique id a bank assigns each debit, carried in most UPI/NEFT SMS and statements. Same ref
= the same real debit anywhere (merge, ignore date); different ref = different debits (never merge).
NULL for card POS txns (no ref — those match by the precise timestamp instead). See RECONCILIATION-SPEC.md.

Revision ID: 029
Revises: 028
"""
import sqlalchemy as sa
from alembic import op

revision = "029"; down_revision = "028"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("bank_ref", sa.String(length=32), nullable=True))
    op.create_index("ix_transactions_bank_ref", "transactions", ["bank_ref"])


def downgrade() -> None:
    op.drop_index("ix_transactions_bank_ref", table_name="transactions")
    op.drop_column("transactions", "bank_ref")
