"""Let a wallet draw say which INVESTMENT it funded, not just which loan.

Wallet spending was wired to loan payments only, so funding a SIP top-up or a PPF contribution from
the wallet had nowhere to record what it went to. A draw whose destination is unknown reduces the
balance without answering the question the wallet exists for: where did that money go.

Revision ID: 059
Revises: 058
"""

import sqlalchemy as sa
from alembic import op

revision = "059"
down_revision = "058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("surplus_draws", sa.Column("recurring_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_surplus_draw_recurring", "surplus_draws",
                          "recurring_investments", ["recurring_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_surplus_draw_recurring", "surplus_draws", type_="foreignkey")
    op.drop_column("surplus_draws", "recurring_id")
