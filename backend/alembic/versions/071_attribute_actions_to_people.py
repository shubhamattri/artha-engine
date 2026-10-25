"""Record WHICH person acted: user_id on the audit trail and on advisor conversations.

`transaction_events.actor` already answered "what kind of thing did this" — user, ai, sync, dedup.
With one login on a household that was also the answer to "who". With several it identifies nobody,
and the audit trail's whole job is being able to ask why a number is what it is.

`advisor_chat.user_id` is a different question: not who wrote it for the record, but whose it is.
The household shares its money; it does not share its questions.

Both are nullable and neither is backfilled. A scheduled sync has no person behind it, and a row
written before this column existed cannot acquire a truthful one — guessing an owner for old rows
would put a name on work nobody did, in the one table that exists to be trustworthy.

The read side treats a NULL advisor_chat.user_id as household-wide rather than hidden, which is what
it was when it was written. That set is fixed and shrinks in relevance as new chats carry an id.

Revision ID: 071
Revises: 070
"""

import sqlalchemy as sa
from alembic import op

revision = "071"
down_revision = "070"
branch_labels = None
depends_on = None

# (table, index name). Both get the same column.
TABLES = [
    ("transaction_events", "ix_transaction_events_user_id"),
    ("advisor_chat", "ix_advisor_chat_user_id"),
]


def _has_table(conn, name: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = :n"), {"n": name}).scalar())


def _has_column(conn, table: str, col: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"), {"t": table, "c": col}).scalar())


def upgrade() -> None:
    # Every step checks the live schema first. Migration 068 died on the server against a database
    # that had drifted ahead of its history, and an idempotent step costs one cheap query.
    conn = op.get_bind()
    for table, index in TABLES:
        if not _has_table(conn, table) or _has_column(conn, table, "user_id"):
            continue
        op.add_column(table, sa.Column("user_id", sa.Integer(), nullable=True))
        op.create_foreign_key(f"fk_{table}_user_id", table, "users", ["user_id"], ["id"])
        op.create_index(index, table, ["user_id"])


def downgrade() -> None:
    conn = op.get_bind()
    for table, index in TABLES:
        if not _has_table(conn, table) or not _has_column(conn, table, "user_id"):
            continue
        op.drop_index(index, table_name=table)
        op.drop_constraint(f"fk_{table}_user_id", table, type_="foreignkey")
        op.drop_column(table, "user_id")
