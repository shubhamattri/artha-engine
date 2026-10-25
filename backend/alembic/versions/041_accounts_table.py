"""Accounts/cards as data, not code.

Which last4s belong to a household lived in a hardcoded dict (services/account_registry.OWN_ACCOUNTS),
so adding a card meant editing Python and redeploying. That is workable for one household and
impossible for a product. This table makes it a user action.

Creates the table EMPTY. The first household's rows are data and are inserted into its database, not
committed here — a migration that ships real names and account numbers would put one household's
personal data into every other household's install. Existing installs backfill from the legacy dict on
first read (see account_registry.own_accounts), so behaviour is unchanged with nothing to re-enter.

Revision ID: 041
Revises: 040
"""

import sqlalchemy as sa
from alembic import op

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"),
                  nullable=False, server_default="1", index=True),
        sa.Column("bank", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="card"),
        sa.Column("last4", sa.String(length=4), nullable=False),
        sa.Column("holder", sa.String(), nullable=True),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("sms_senders", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_accounts_household_last4", "accounts", ["household_id", "last4"])


def downgrade() -> None:
    op.drop_index("ix_accounts_household_last4", table_name="accounts")
    op.drop_table("accounts")
