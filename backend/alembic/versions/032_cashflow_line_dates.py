"""cashflow_lines.start_date / end_date — so a fixed expense / income line is only a commitment for the
months it actually exists (a pocket-money line added in June isn't 'missing' for January).

Revision ID: 032
Revises: 031
"""
import sqlalchemy as sa
from alembic import op

revision = "032"; down_revision = "031"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.add_column("cashflow_lines", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("cashflow_lines", sa.Column("end_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("cashflow_lines", "end_date")
    op.drop_column("cashflow_lines", "start_date")
