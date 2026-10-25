"""One home for a category's settings, whoever created the category.

060 put active / is_variable / color / default_target_inr on custom_categories, which only exist for
categories a household invented. 061 then added the same four properties in category_settings so they
could apply to built-ins too. That left two sources for one fact, and the two disagreed the moment a
custom category was marked committed in one and read from the other.

custom_categories keeps what it is actually for: the IDENTITY of a category the household added, which
the built-in enum cannot express. Everything about how a category behaves or looks now lives in
category_settings, keyed by canonical name, for built-in and custom alike.

Values from 060 are carried across before the columns go, so a household that set something in the
half hour those columns existed does not lose it.

Revision ID: 062
Revises: 061
"""

import sqlalchemy as sa
from alembic import op

revision = "062"
down_revision = "061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Carry anything already set. INSERT ... SELECT rather than an ORM loop so this stays a single
    # statement regardless of how many rows a household has.
    op.execute("""
        INSERT INTO category_settings
            (household_id, category, color, active, is_variable, sort_order, default_target_inr)
        SELECT c.household_id, c.name, c.color, c.active, c.is_variable, c.sort_order,
               c.default_target_inr
        FROM custom_categories c
        WHERE NOT EXISTS (
            SELECT 1 FROM category_settings s
            WHERE s.household_id = c.household_id AND s.category = c.name
        )
    """)
    op.drop_column("custom_categories", "default_target_inr")
    op.drop_column("custom_categories", "color")
    op.drop_column("custom_categories", "is_variable")
    op.drop_column("custom_categories", "active")


def downgrade() -> None:
    op.add_column("custom_categories",
                  sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("custom_categories",
                  sa.Column("is_variable", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("custom_categories", sa.Column("color", sa.String(), nullable=True))
    op.add_column("custom_categories", sa.Column("default_target_inr", sa.Float(), nullable=True))
    op.execute("""
        UPDATE custom_categories c SET
            color = s.color,
            active = COALESCE(s.active, TRUE),
            is_variable = COALESCE(s.is_variable, TRUE),
            default_target_inr = s.default_target_inr
        FROM category_settings s
        WHERE s.household_id = c.household_id AND s.category = c.name
    """)
