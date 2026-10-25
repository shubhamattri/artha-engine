"""Per-household card statement days.

`per_card_cycles`, `card_cycle_days` and `card_cycle_default` were global settings, so every household
shared one set of credit-card billing days. Same class of bug as 039 (cycle_start_day). Null keeps the
settings fallback, so existing installs are unchanged.

Revision ID: 040
Revises: 039
"""

import sqlalchemy as sa
from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("households", sa.Column("per_card_cycles", sa.Boolean(), nullable=True))
    op.add_column("households", sa.Column("card_cycle_days", sa.JSON(), nullable=True))
    op.add_column("households", sa.Column("card_cycle_default", sa.Integer(), nullable=True))


def downgrade() -> None:
    for c in ("card_cycle_default", "card_cycle_days", "per_card_cycles"):
        op.drop_column("households", c)
