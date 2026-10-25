"""commitment_links — learned payee ↔ recurring-commitment associations

Lets the reconciler match a debit to its commitment by IDENTITY (learned payee), not a hardcoded name
or amount threshold. Two ₹7,000 debits (cook + maid) are told apart by who they're paid to; a first/last
EMI still links despite a different amount. Seeded from confident matches + user confirmation.

Revision ID: 030
Revises: 029
"""
import sqlalchemy as sa
from alembic import op

revision = "030"; down_revision = "029"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.create_table(
        "commitment_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("signature", sa.String(), nullable=False),
        sa.Column("commitment_ref", sa.String(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("household_id", "signature", name="uq_commitment_link_sig"),
    )
    op.create_index("ix_commitment_links_household_id", "commitment_links", ["household_id"])
    op.create_index("ix_commitment_links_signature", "commitment_links", ["signature"])


def downgrade() -> None:
    op.drop_index("ix_commitment_links_signature", table_name="commitment_links")
    op.drop_index("ix_commitment_links_household_id", table_name="commitment_links")
    op.drop_table("commitment_links")
