"""asset.meta JSON — type-specific fields (gold purity; vehicle purchase price/date)

Revision ID: 014
Revises: 013
"""
import sqlalchemy as sa
from alembic import op
revision = "014"; down_revision = "013"; branch_labels = None; depends_on = None

def upgrade() -> None:
    op.add_column("assets", sa.Column("meta", sa.JSON(), nullable=True))

def downgrade() -> None:
    op.drop_column("assets", "meta")
