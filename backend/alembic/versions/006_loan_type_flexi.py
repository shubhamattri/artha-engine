"""loan_type + flexi/moratorium fields

Revision ID: 006
Revises: 005
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("loans", sa.Column("loan_type", sa.String(), nullable=False,
                                     server_default="standard"))  # standard | flexi
    op.add_column("loans", sa.Column("disbursed_amount", sa.Float(), nullable=True))  # drawn so far
    op.add_column("loans", sa.Column("moratorium_months", sa.Integer(), nullable=False,
                                     server_default="0"))
    op.add_column("loans", sa.Column("moratorium_interest_mode", sa.String(), nullable=True))
    # full | partial | none — how moratorium interest is serviced


def downgrade() -> None:
    for c in ("loan_type", "disbursed_amount", "moratorium_months", "moratorium_interest_mode"):
        op.drop_column("loans", c)
