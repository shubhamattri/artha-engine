"""deleted_fingerprints — tombstones so a hard-deleted txn never gets re-synced back

The 3-hourly sync re-reads the last few days of SMS/email and inserts anything whose fingerprint
isn't already in the DB. A HARD delete removes the row (and its fingerprint), so the next sync
re-created it — entries kept coming back. We now record the fingerprint of every hard-deleted txn
here; ingest skips any fingerprint in this table. (Archived rows keep their fingerprint, so they
were already safe; this covers true deletes.)

Revision ID: 023
Revises: 022
"""
import sqlalchemy as sa
from alembic import op

revision = "023"; down_revision = "022"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.create_table(
        "deleted_fingerprints",
        sa.Column("fingerprint", sa.String(length=40), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("deleted_fingerprints")
