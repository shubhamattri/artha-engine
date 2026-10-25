"""recurring_investments.skips — per-month partial SIP skips.

A skip records that a given month's contribution was reduced by an amount (e.g. to free cash during an
over-budget cycle without pausing the whole SIP). Shape: [{"month": "YYYY-MM", "amount": float}].

Revision ID: 031
Revises: 030
"""
import sqlalchemy as sa
from alembic import op

revision = "031"; down_revision = "030"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.add_column("recurring_investments", sa.Column("skips", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("recurring_investments", "skips")
