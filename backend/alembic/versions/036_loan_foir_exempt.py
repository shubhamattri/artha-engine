"""loans.foir_exempt — an external loan (e.g. a parent's gold loan against family gold) that pledges
collateral but is NOT the household's cash-flow: kept out of FOIR / EMI total / net-worth liabilities /
payoff strategies, while still counting as pledged gold.

Revision ID: 036
Revises: 035
"""
import sqlalchemy as sa
from alembic import op

revision = "036"; down_revision = "035"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.add_column("loans", sa.Column("foir_exempt", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("loans", "foir_exempt")
