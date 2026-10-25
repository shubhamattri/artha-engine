"""Whether a lender accepts money early, which decides where spare cash can go.

The payoff simulator cascades spare cash down the strategy order and part-pays whatever it reaches.
Real lenders often refuse that: most car and personal loans take nothing until you can clear the
whole balance, and some are locked in entirely. Modelling every loan as freely part-payable produces
a plan that sends money at a loan that will send it back, so the closure dates it promises cannot
happen.

  part            any extra reduces principal (floating home loans by RBI rule, gold, most flexi)
  foreclose_only  refuses part payment; accepts a full payoff
  none            refuses early money altogether

Both are NULLABLE on purpose: NULL means "not overridden", and services.prepayment derives the answer
from the rate type. For a floating-rate individual borrower the RBI decides this, so asking the user
to type it in would be asking them to restate the law.

Revision ID: 050
Revises: 049
"""

import sqlalchemy as sa
from alembic import op

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("loans", sa.Column("prepayment", sa.String(), nullable=True))
    op.add_column("loans", sa.Column("prepayment_charge_pct", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("loans", "prepayment_charge_pct")
    op.drop_column("loans", "prepayment")
