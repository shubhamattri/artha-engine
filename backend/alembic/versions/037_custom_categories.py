"""custom_categories — household-defined budget categories (added from the Plan UI)

Extends the built-in taxonomy with the household's own everyday-spend buckets. Each row is a variable
spend category; no enum change is needed (Transaction.category is a free string and CATEGORY_TYPE.get
defaults unknown names to SPEND). Name is unique per household.

Revision ID: 037
Revises: 036
"""
import sqlalchemy as sa
from alembic import op

revision = "037"; down_revision = "036"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_categories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"),
                  nullable=False, server_default="1"),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("household_id", "name", name="uq_custom_category_household_name"),
    )
    op.create_index("ix_custom_categories_household_id", "custom_categories", ["household_id"])


def downgrade() -> None:
    op.drop_index("ix_custom_categories_household_id", table_name="custom_categories")
    op.drop_table("custom_categories")
