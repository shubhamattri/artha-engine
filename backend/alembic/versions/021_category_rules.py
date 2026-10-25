"""category_rules — learned merchant→category memory (AI only on first sight)

A key-value store: signature (normalized merchant) → category + clean merchant name. The classifier
checks this FIRST; only unseen merchants go to AI, whose answer is then saved here. A user editing a
transaction's category writes a 'user' rule (highest trust) so the same merchant is right forever.

Revision ID: 021
Revises: 020
"""
import sqlalchemy as sa
from alembic import op

revision = "021"; down_revision = "020"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.create_table(
        "category_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("signature", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("merchant", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False, server_default="ai"),  # user | ai | rule
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_category_rules_signature", "category_rules", ["signature"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_category_rules_signature", table_name="category_rules")
    op.drop_table("category_rules")
