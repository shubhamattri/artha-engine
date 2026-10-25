"""transactions.source — statement | manual | gmail (manual = daily-logged spend)

Revision ID: 019
Revises: 018
"""
import sqlalchemy as sa
from alembic import op
revision="019"; down_revision="018"; branch_labels=None; depends_on=None

def upgrade() -> None:
    op.add_column("transactions", sa.Column("source", sa.String(), nullable=False, server_default="statement"))

def downgrade() -> None:
    op.drop_column("transactions", "source")
