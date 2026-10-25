"""Per-household cycle start day.

The spending cycle anchor lived in `settings.cycle_start_day` — one global value shared by every
household. Household #2 would silently inherit household #1's billing day. It now lives on the
household row; null keeps the settings fallback so existing installs are unchanged.

Revision ID: 039
Revises: 038
"""

import sqlalchemy as sa
from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("households", sa.Column("cycle_start_day", sa.Integer(), nullable=True))
    # Backfill every existing household with the global value they were effectively already using,
    # so behaviour is identical the moment this lands (no silent cycle shift on deploy).
    from config import settings

    day = max(1, min(31, int(getattr(settings, "cycle_start_day", 12) or 12)))
    op.execute(sa.text("UPDATE households SET cycle_start_day = :d WHERE cycle_start_day IS NULL")
               .bindparams(d=day))


def downgrade() -> None:
    op.drop_column("households", "cycle_start_day")
