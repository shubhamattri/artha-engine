"""Give a custom category the properties a household needs to manage it.

A category could be created and renamed and little else. Everything a household actually does with its
own taxonomy over time — retire one without losing its history, stop it counting toward the everyday
Plan, give it a stable colour, set the target once instead of every month — had nowhere to live.

  active            retire a category without deleting its past. Deleting reassigns history to another
                    bucket, which is right when the category was a mistake and wrong when it simply
                    stopped being used. Archiving keeps the past truthful and takes it out of pickers.
  is_variable       whether it counts toward the everyday Plan pool. Custom categories were variable by
                    definition; a household's own "Rent" or "School fees" is a committed cost that must
                    NOT be judged against the weekly spending pace.
  color             the chart palette is indexed by position, so adding a category silently recoloured
                    the ones after it. A stored colour makes it the category's own.
  default_target_inr  a target set once on the category, seeding new months, instead of retyping it
                    into each month's budget.

Revision ID: 060
Revises: 059
"""

import sqlalchemy as sa
from alembic import op

revision = "060"
down_revision = "059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default on the booleans so existing rows get the pre-migration behaviour: every category
    # that exists today is active, and every one of them is a variable spend bucket, which is exactly
    # what the model docstring promised before this change.
    op.add_column("custom_categories",
                  sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("custom_categories",
                  sa.Column("is_variable", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("custom_categories", sa.Column("color", sa.String(), nullable=True))
    op.add_column("custom_categories", sa.Column("default_target_inr", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_categories", "default_target_inr")
    op.drop_column("custom_categories", "color")
    op.drop_column("custom_categories", "is_variable")
    op.drop_column("custom_categories", "active")
