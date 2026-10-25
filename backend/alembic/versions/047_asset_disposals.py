"""Record selling something.

There was no way to express a sale. The only lever on an asset was `active`, which loses the price,
the date and the gain, so the proceeds had to be hand-entered as a fake cash asset. That workaround is
in the live data: "Maple Heights sale proceeds (receivable)" is a manual cash row standing in for a
sale the model could not represent. Net worth then double-counts, because the asset is still active
alongside the row invented for its proceeds.

The fields are deliberately granular rather than one net number, because the tax answer and the
cash answer come apart:

    Maple Heights      sold Rs 50,00,000, left Rs 12,00,000 in hand.
                       The capital gain is computed on Rs 50,00,000.

`loan_settled_inr` is what closes that gap, and it is kept apart from `costs_inr` on purpose: costs of
transfer reduce the taxable gain, proceeds used to repay a loan reduce only what reaches your bank.
Merging them understates tax owed.

`cost_basis_inr` is nullable because it is sometimes genuinely unknown at the time of recording. For
ESOP shares the basis is the FMV on each exercise date (already taxed as a perquisite), NOT the
discounted price paid, and that figure lives in the plan administrator's statements. Null means "gain
not computable yet" rather than "gain is zero".

Revision ID: 047
Revises: 046
"""

import sqlalchemy as sa
from alembic import op

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_disposals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("disposed_on", sa.Date(), nullable=False),
        sa.Column("acquired_on", sa.Date(), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("gross_proceeds_inr", sa.Float(), nullable=False),
        sa.Column("costs_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("loan_settled_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("receivable_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cost_basis_inr", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_asset_disposals_household_id", "asset_disposals", ["household_id"])
    op.create_index("ix_asset_disposals_asset_id", "asset_disposals", ["asset_id"])


def downgrade() -> None:
    op.drop_index("ix_asset_disposals_asset_id", table_name="asset_disposals")
    op.drop_index("ix_asset_disposals_household_id", table_name="asset_disposals")
    op.drop_table("asset_disposals")
