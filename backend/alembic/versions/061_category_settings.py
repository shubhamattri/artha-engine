"""Let a household manage EVERY category, not only the ones it invented.

Category management landed against custom_categories, which is a table, while the built-in taxonomy is
a Python enum. That split is an implementation detail and it leaked all the way to the screen: a
household whose categories are all built-in (the normal case, and the case for this one) opened
"Manage categories" and found an empty list. Groceries, Travel and Personal / Misc are the categories
people actually use, and nothing could be done to any of them.

This table holds per-category settings keyed by the CANONICAL category name, so the same colour,
target, archive and committed flags work for a built-in and a custom category alike.

Naming is the one thing that cannot be uniform, and the constraint is real rather than cosmetic. Forty
places compare against Category.*.value, including the AI classifier's prompt, which embeds the names
as literal text. Rewriting a built-in's stored name would drop it out of the Plan pool and change what
the classifier is told. So a built-in gets `display_name`: the key stays "Groceries", the household
sees "Kirana". A custom category has no such constraint and keeps its existing real rewrite.

Revision ID: 061
Revises: 060
"""

import sqlalchemy as sa
from alembic import op

revision = "061"
down_revision = "060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "category_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"),
                  nullable=False, index=True, server_default="1"),
        # The canonical name as stored on transactions. For a built-in that is Category.X.value; for a
        # custom category it is its own name, which rename keeps in step by rewriting both.
        sa.Column("category", sa.String(), nullable=False),
        # NULL = show the canonical name. Only ever a label; nothing joins on it.
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("color", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        # NULL = fall back to the taxonomy's own answer, so a built-in stays whatever it has always
        # been unless the household deliberately says otherwise.
        sa.Column("is_variable", sa.Boolean(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("default_target_inr", sa.Float(), nullable=True),
        sa.UniqueConstraint("household_id", "category", name="uq_category_settings_household_category"),
    )


def downgrade() -> None:
    op.drop_table("category_settings")
