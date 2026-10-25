"""Collect the identity banks require, at onboarding.

Statement PDFs are locked with a password derived from the holder's name, date of birth, mobile, PAN
or PIN code. The app never asked for any of it, so statement import silently could not open most
files — an onboarding gap, not a per-card configuration problem.

Per MEMBER rather than per household: a card in one spouse's name is locked with that spouse's
details, so a single household-level profile would fail for half the cards.

Revision ID: 042
Revises: 041
"""

import sqlalchemy as sa
from alembic import op

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("dob", sa.Date(), nullable=True))
    op.add_column("members", sa.Column("mobile", sa.String(), nullable=True))
    op.add_column("members", sa.Column("pan", sa.String(), nullable=True))
    op.add_column("members", sa.Column("pincode", sa.String(), nullable=True))


def downgrade() -> None:
    for c in ("pincode", "pan", "mobile", "dob"):
        op.drop_column("members", c)
