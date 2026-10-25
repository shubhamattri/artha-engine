"""Let two different files cover the same statement month.

The constraint was UNIQUE(bank, card_type, stmt_month), so one month meant one record. A bank sends
more than one file for a month: a mid-cycle "Current" export listing unbilled transactions, and
later the billed statement listing the same purchases in a different format ("BDSKAMAZONPAYIN"
becomes "40012345678 AMAZON PAY IN GROCERY"). Both landed in the same record.

The rows then became indistinguishable by origin. On one card, id 4452 (written 23 Jun from the
export) and id 5403 (written 11 Aug from the billed PDF) both carry statement_id=10. That made the
double-count uncleanable: keying a repair on statement_id would have marked the BILLED rows as
provisional and let the next import archive the authoritative copy, turning a double-count into
data loss.

Adding filename to the key is additive. It only permits rows the old constraint rejected; nothing
existing violates it, and no data is rewritten.

Revision ID: 054
Revises: 053
"""

from alembic import op

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_statement", "statements", type_="unique")
    op.create_unique_constraint(
        "uq_statement", "statements", ["bank", "card_type", "stmt_month", "filename"])


def downgrade() -> None:
    # Reversible only while no month holds two files. If one does, the old constraint cannot be
    # recreated and this raises rather than silently deleting a statement to make room.
    op.drop_constraint("uq_statement", "statements", type_="unique")
    op.create_unique_constraint("uq_statement", "statements", ["bank", "card_type", "stmt_month"])
