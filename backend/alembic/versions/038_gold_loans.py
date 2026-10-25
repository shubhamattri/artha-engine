"""gold loans — flexible payment ledger + decoupled serviced/FOIR flags

Adds loans.serviced_by_household (whether the household pays the EMI → cash-flow + net-worth) and
loans.expected_monthly_payment (the nominal ₹ for a gold loan's leftover projection), and the
loan_payments ledger (any amount, any date, many or none). Backfills existing foir_exempt loans to
serviced_by_household=False so their behaviour (fully external: off cash-flow + off net-worth) is
preserved exactly — the new gold-loan case sets foir_exempt=True BUT serviced_by_household=True.

Revision ID: 038
Revises: 037
"""
import sqlalchemy as sa
from alembic import op

revision = "038"; down_revision = "037"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.add_column("loans", sa.Column("serviced_by_household", sa.Boolean(), nullable=False,
                                     server_default="1"))
    op.add_column("loans", sa.Column("expected_monthly_payment", sa.Float(), nullable=True))
    # Preserve existing behaviour: a foir_exempt loan was fully external (off cash-flow AND net-worth).
    op.execute("UPDATE loans SET serviced_by_household = false WHERE foir_exempt = true")

    op.create_table(
        "loan_payments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("loan_id", sa.Integer(), sa.ForeignKey("loans.id"), nullable=False),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"),
                  nullable=False, server_default="1"),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_loan_payments_loan_id", "loan_payments", ["loan_id"])
    op.create_index("ix_loan_payments_household_id", "loan_payments", ["household_id"])


def downgrade() -> None:
    op.drop_index("ix_loan_payments_household_id", table_name="loan_payments")
    op.drop_index("ix_loan_payments_loan_id", table_name="loan_payments")
    op.drop_table("loan_payments")
    op.drop_column("loans", "expected_monthly_payment")
    op.drop_column("loans", "serviced_by_household")
