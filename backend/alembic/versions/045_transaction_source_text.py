"""Keep the text a transaction was parsed from.

Parsing bank alerts is lossy and the misses only surface later. ICICI writes "Acc XX456"; the
extractor produced "0456", a card that does not exist, and 30 real transactions sat under it. By
then the source message was gone from the pipeline, so the only repair was re-fetching from the
mailbox and matching on amount — which fails once the mail ages out.

Storing the source makes any future extraction bug retroactively fixable from the database alone.

Revision ID: 045
Revises: 044
"""

import sqlalchemy as sa
from alembic import op

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("source_text", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "source_text")
