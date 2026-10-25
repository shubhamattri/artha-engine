"""What a recurring contribution is FOR, separate from the instrument it sits in.

An RD funding a donation is still an RD. The instrument says how the money is held; it says nothing
about whether the money ever becomes household wealth. Without this the donation RDs counted as
investments, inflating the invested figure and skewing the debt/equity ratio with money that is
already spoken for.

Defaults to 'wealth' so every existing row keeps its current meaning. The contribution stays in
takehome_outflow either way — it is a real bank debit regardless of purpose.

Revision ID: 048
Revises: 047
"""

import sqlalchemy as sa
from alembic import op

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recurring_investments",
                  sa.Column("purpose", sa.String(), nullable=False, server_default="wealth"))


def downgrade() -> None:
    op.drop_column("recurring_investments", "purpose")
