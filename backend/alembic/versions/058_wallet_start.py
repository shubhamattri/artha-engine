"""The wallet starts counting from one named cycle, not from all of history.

The first version counted every closed cycle it could find. Cycles from before tracking began had no
recorded spend, so pool-minus-nothing read as a full month saved and the balance came out at
Rs 30 lakh. Requiring transaction data fixed the worst of it, but thin early cycles still leaked
in: a cycle contributing Rs 1,95,000 of a Rs 2,17,500 pool is a cycle we barely recorded, not a
month of extraordinary thrift.

A named start cycle is the honest boundary. Everything before it is excluded because the data does
not support a claim either way, and the wallet only makes promises about cycles it actually watched.

Per-household, not a constant: household B starts whenever household B starts.

Revision ID: 058
Revises: 057
"""

import sqlalchemy as sa
from alembic import op

revision = "058"
down_revision = "057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL means the wallet has not started. That is deliberately the default: a wallet that
    # silently backfills history is how the Rs 30 lakh balance happened.
    op.add_column("households",
                  sa.Column("wallet_start_cycle_end", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("households", "wallet_start_cycle_end")
