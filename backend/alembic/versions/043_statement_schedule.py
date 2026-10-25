"""Per-card statement schedule, so the monthly audit is keyed to each card's own date.

A statement exists once a month, on a date the bank chooses per card. Fetching it from a nightly job
is the wrong shape: it either runs 30 times uselessly or misses the one day that mattered. These
columns let each card carry its own schedule, discovered from the mailbox rather than typed in.

statement_sender is the key rather than the card number because banks mask the number in filenames
and subjects ("4854XXXXXXXXXX42"), while the from-address is stable.

Revision ID: 043
Revises: 042
"""

import sqlalchemy as sa
from alembic import op

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("statement_sender", sa.String(), nullable=True))
    op.add_column("accounts", sa.Column("statement_day", sa.Integer(), nullable=True))
    op.add_column("accounts", sa.Column("statement_run_day", sa.Integer(), nullable=True))
    op.add_column("accounts", sa.Column("statement_last_imported", sa.String(), nullable=True))


def downgrade() -> None:
    for c in ("statement_last_imported", "statement_run_day", "statement_day", "statement_sender"):
        op.drop_column("accounts", c)
