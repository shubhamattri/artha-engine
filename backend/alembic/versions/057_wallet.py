"""Make the surplus behave like a wallet: it accrues by itself and is spent from anywhere.

The first cut required earmarking each cycle's leftover into an allocation before it could be spent.
That is friction in the wrong place — the money exists whether or not it has been categorised, and a
step you must remember is a step that gets skipped, leaving the wallet permanently understating what
is there.

So draws now hang off the HOUSEHOLD, not an allocation: the balance is every closed cycle's saving
minus everything drawn. Allocations remain optional, for earmarking a purpose when that is useful.

Revision ID: 057
Revises: 056
"""

import sqlalchemy as sa
from alembic import op

revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("surplus_draws", sa.Column("household_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_surplus_draw_hh", "surplus_draws", "households",
                          ["household_id"], ["id"])
    # Existing draws belong to their allocation's household; there are none in production yet, but
    # a migration that silently leaves orphans is how a balance starts drifting.
    op.execute("""
        UPDATE surplus_draws SET household_id = (
            SELECT household_id FROM surplus_allocations a WHERE a.id = surplus_draws.allocation_id)
        WHERE household_id IS NULL
    """)
    op.alter_column("surplus_draws", "allocation_id", nullable=True)
    op.create_index("ix_surplus_draw_hh", "surplus_draws", ["household_id"])
    # Where the money came from, so "deduct from the wallet or tell me another source" is answerable
    # after the fact rather than assumed.
    op.add_column("surplus_draws", sa.Column("funded_from", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("surplus_draws", "funded_from")
    op.drop_index("ix_surplus_draw_hh", table_name="surplus_draws")
    op.alter_column("surplus_draws", "allocation_id", nullable=False)
    op.drop_constraint("fk_surplus_draw_hh", "surplus_draws", type_="foreignkey")
    op.drop_column("surplus_draws", "household_id")
