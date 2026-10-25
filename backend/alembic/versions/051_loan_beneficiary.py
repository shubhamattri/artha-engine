"""Who the borrowing is FOR, which is not the same as who pays it.

A loan in your name for a sibling is your obligation to SERVICE and their principal to clear. The
payoff engine was putting one such loan first in the avalanche and spending Rs 1,00,000 a month of
the household's money for ten months clearing it. The EMI is theirs to pay; the prepayment is their
wealth, funded by this household.

Two flags already existed and neither says this. serviced_by_household answers "does the money leave
my account". foir_exempt answers "does it count against my borrowing eligibility". Neither answers
"is accelerating this a good use of MY money".

  household  normal: EMI in cash-flow, spare cash welcome, accelerate
  member     a specific member's, still within the household
  external   EMI stays in cash-flow and the balance stays a liability, but spare cash goes elsewhere

Defaults to 'household' so every existing loan behaves exactly as before.

Revision ID: 051
Revises: 050
"""

import sqlalchemy as sa
from alembic import op

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("loans", sa.Column("beneficiary", sa.String(), nullable=False,
                                     server_default="household"))


def downgrade() -> None:
    op.drop_column("loans", "beneficiary")
