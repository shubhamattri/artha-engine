"""loans.rate_type — fixed | floating (floating tenure flexes as the rate changes)

Revision ID: 016
Revises: 015
"""
import sqlalchemy as sa
from alembic import op
revision="016"; down_revision="015"; branch_labels=None; depends_on=None

def upgrade() -> None:
    op.add_column("loans", sa.Column("rate_type", sa.String(), nullable=False, server_default="fixed"))
    # Home loans are usually repo-linked floating
    op.execute("UPDATE loans SET rate_type='floating' WHERE name ILIKE '%home loan%'")

def downgrade() -> None:
    op.drop_column("loans", "rate_type")
