"""Record an EMI that did NOT happen, not just extra money that did.

compute_state derives a balance from months elapsed, so it assumes every scheduled EMI was paid. Skip
one and the app shows a balance lower than reality and a payoff date earlier than reality, with
nothing to say otherwise.

An 'extra' row is money on top of the EMI. A 'skipped_emi' row is the scheduled EMI that never left
the account. One ledger, because both answer the same question: what really moved, versus what the
plan assumed. Defaults to 'extra' so every existing row keeps its meaning.

Revision ID: 052
Revises: 051
"""

import sqlalchemy as sa
from alembic import op

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("loan_payments",
                  sa.Column("kind", sa.String(), nullable=False, server_default="extra"))


def downgrade() -> None:
    op.drop_column("loan_payments", "kind")
