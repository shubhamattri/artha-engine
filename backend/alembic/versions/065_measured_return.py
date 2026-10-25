"""A return the household measured somewhere this app cannot see.

Every fund was re-anchored to today against the broker, so there is no in-app redemption history left
to derive an XIRR from and api/windfall had no `observed` anchor at all — every scenario on the page
was `assumed`. The real figure exists, at the broker. These columns are how it gets in.

Four columns, not one, and that is the point. A hand-entered rate rots silently, which is the exact
failure the windfall page exists to end, so the rate may not be stored without the date it measures
TO (`as_of`), the moment a human typed it (`entered_at`) and what it refers to (`scope`). NULL
everywhere means nothing was entered, and nothing is substituted in its place.

Revision ID: 065
Revises: 064
"""

import sqlalchemy as sa
from alembic import op

revision = "065"
down_revision = "064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("households", sa.Column("measured_return_pct", sa.Float(), nullable=True))
    op.add_column("households", sa.Column("measured_return_scope", sa.String(), nullable=True))
    op.add_column("households", sa.Column("measured_return_as_of", sa.Date(), nullable=True))
    op.add_column("households", sa.Column("measured_return_entered_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("households", "measured_return_entered_at")
    op.drop_column("households", "measured_return_as_of")
    op.drop_column("households", "measured_return_scope")
    op.drop_column("households", "measured_return_pct")
