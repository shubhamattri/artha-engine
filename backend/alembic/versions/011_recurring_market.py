"""recurring: market-valuation blob for market-linked instruments (SIP/MF).

One nullable JSON column (like flexi_terms) holds {scheme_code, units, last_nav,
last_nav_at}. When present, current value = units × live NAV (fetched), instead of
the fixed-rate accrual projection. NULL for RD/FD/EPF/PPF.

Revision ID: 011
Revises: 010
"""

import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recurring_investments", sa.Column("market", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("recurring_investments", "market")
