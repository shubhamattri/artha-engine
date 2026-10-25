"""recurring: from_takehome flag — does this contribution debit the bank account
(SIP/RD/PPF) or is it deducted at source (EPF employee + employer)? Only
take-home contributions reduce the monthly-engine leftover.

Revision ID: 010
Revises: 009
"""

import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recurring_investments",
                  sa.Column("from_takehome", sa.Boolean(), nullable=False, server_default=sa.true()))
    # EPF is deducted at source — never from take-home.
    op.execute("UPDATE recurring_investments SET from_takehome = false WHERE kind = 'epf'")


def downgrade() -> None:
    op.drop_column("recurring_investments", "from_takehome")
