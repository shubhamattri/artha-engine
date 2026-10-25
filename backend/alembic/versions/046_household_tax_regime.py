"""Record which tax regime a household is on, and which properties are let out.

The payoff engine ranked loans by headline rate, which is the wrong number whenever a deduction is in
play: a 9.5% education loan whose interest is fully deductible under 80E costs less than a 9.1% gold
loan that gets no relief, so the old ordering pointed at the cheapest debt first.

Whether any of that applies is not inferable from the data — under the new regime (the statutory
default since FY 2023-24) 80C, 80E and 24(b)-for-self-occupied do not exist at all, and the nominal
ranking is already correct. So it has to be stored per household.

Null means "new regime": that is the default a household lands on without opting out, and it is the
conservative choice here because it claims no relief that may not exist.

`let_out_members` lists member keys whose house property is let out. It matters because the ₹2,00,000
24(b) cap applies only to self-occupied property — let-out interest stays fully deductible.

Revision ID: 046
Revises: 045
"""

import sqlalchemy as sa
from alembic import op

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("households", sa.Column("tax_regime", sa.String(), nullable=True))
    op.add_column("households", sa.Column("let_out_members", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("households", "let_out_members")
    op.drop_column("households", "tax_regime")
