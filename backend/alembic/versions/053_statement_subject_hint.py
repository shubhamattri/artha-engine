"""Tell two cards apart when their bank mails both statements from one address.

statement_sender was assumed to identify a card. It does not. ICICI mails the first card's
statement on the 13th and the second card's on the 3rd, both from credit_cards@icici.bank.in. Keyed
on sender alone the two schedules merge into one series that lands on the 3rd and the 13th, which
reads as a 10-day spread and gets marked unstable — so the discovery excluded the two cards that
needed it most, while HDFC and Kotak (one card per sender) looked fine.

The subject distinguishes them and the bank keeps it stable: "Amazon Pay ICICI Bank Credit Card
Statement for the period ..." versus "ICICI Bank Credit Card Statement for the period ...". This
column holds that discriminator, so an account is matched on (sender, subject contains hint).

NULL means the sender alone identifies the card, which is true for every other issuer here.

Revision ID: 053
Revises: 052
"""

import sqlalchemy as sa
from alembic import op

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("statement_subject_hint", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "statement_subject_hint")
