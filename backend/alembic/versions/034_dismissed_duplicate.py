"""dismissed_duplicate — remembers pairs the user marked 'not a duplicate' in the review queue.

Revision ID: 034
Revises: 033
"""
import sqlalchemy as sa
from alembic import op

revision = "034"; down_revision = "033"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.create_table(
        "dismissed_duplicate",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"),
                  nullable=False, server_default="1"),
        sa.Column("fp_lo", sa.String(40), nullable=False),
        sa.Column("fp_hi", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("household_id", "fp_lo", "fp_hi", name="uq_dismissed_pair"),
    )
    op.create_index("ix_dismissed_duplicate_household_id", "dismissed_duplicate", ["household_id"])


def downgrade() -> None:
    op.drop_index("ix_dismissed_duplicate_household_id", table_name="dismissed_duplicate")
    op.drop_table("dismissed_duplicate")
