"""When a fund's real transactions are recorded, stop inventing them.

`api/recurring.py` builds a fund's cash flows from three sources: the opening balance at its anchor,
every entry in `contributions`, AND a synthetic monthly flow generated from `monthly_contribution`
for every month since the anchor. That third source is how a fund with no recorded ledger still
accrues sensibly, and it is correct as long as the ledger is empty.

The moment a real transaction history is imported — from a CAS statement, or entered by hand — those
two sources describe the SAME money. Every instalment would be counted twice: once as the recorded
entry and once as the invented one. That inflates `total_contributed`, inflates the invested base
XIRR is measured against, and does it silently, which is the failure mode this app keeps having to
design against.

So the ledger has to be able to say it is complete. NULL/false means what it means today: the ledger
holds only ad-hoc entries and the monthly flow is still generated. True means the recorded ledger IS
the history and nothing may be invented on top of it.

`monthly_contribution` deliberately keeps its meaning either way — it is still what leaves the bank
each month, still what "going in monthly" reports, and still what cash-flow subtracts. Zeroing it to
suppress the generator would have been the cheap fix and would have lied about the standing
instruction.

Revision ID: 067
Revises: 066
"""

import sqlalchemy as sa
from alembic import op

revision = "067"
down_revision = "066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recurring_investments",
        sa.Column("ledger_is_authoritative", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("recurring_investments", "ledger_is_authoritative")
