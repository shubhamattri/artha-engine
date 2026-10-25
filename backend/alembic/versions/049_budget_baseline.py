"""When a household started tracking, so pre-tracking spend stops driving suggested targets.

A household that begins budgeting is deliberately changing its behaviour. The months before that are
what spending looks like with no visibility — the exact problem being fixed. Feeding them into the
recommendation engine makes it advise raising targets back toward the old behaviour, which reads as
"give up". Periods before this date are excluded from suggestions.

Null keeps the previous behaviour (use everything), so existing households are unaffected.

Revision ID: 049
Revises: 048
"""

import sqlalchemy as sa
from alembic import op

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("households", sa.Column("budget_baseline_from", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("households", "budget_baseline_from")
