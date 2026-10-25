"""transactions.merchant — clean display name from AI categorization (nullable)

Revision ID: 018
Revises: 017
"""
import sqlalchemy as sa
from alembic import op
revision="018"; down_revision="017"; branch_labels=None; depends_on=None

def upgrade() -> None:
    op.add_column("transactions", sa.Column("merchant", sa.String(), nullable=True))

def downgrade() -> None:
    op.drop_column("transactions", "merchant")
