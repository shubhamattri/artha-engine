"""Record each sync run, so a sync that stops is visible instead of inferred.

The sync had no heartbeat. When it stopped, nothing said so: the app kept serving the figures it
already had, and the only symptom was a purchase not appearing. A quiet week and a dead job look
identical from the outside, which is why this was found by a person and not by the system.

`ok` separates the two failures that need different fixes: a job that is not running at all, and a
job that runs and fails every time.

Revision ID: 055
Revises: 054
"""

import sqlalchemy as sa
from alembic import op

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
    )
    op.create_index("ix_sync_runs_started_at", "sync_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_sync_runs_started_at", table_name="sync_runs")
    op.drop_table("sync_runs")
