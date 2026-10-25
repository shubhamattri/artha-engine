"""Tenant-scope the four sync/dedup tables.

`statements`, `deleted_fingerprints`, `evaluated_sms` and `sync_runs` carry no household, so the
endpoints that use them cannot filter by one even in principle. Everything else in the schema is
already tenanted. Nine endpoints operated on every row in the database as a result.

Two of these tables key on `fingerprint` alone, which is not merely un-scoped but actively wrong once
a second household exists: household B could not tombstone a transaction whose fingerprint household
A had already tombstoned, and B's SMS would be skipped unread because A's identical SMS was judged
non-spend. Both primary keys become composite.

**Written to run against a schema that has already drifted.** The first version assumed a pristine
database and died on the server with "column household_id of relation statements already exists" —
that column was there, added outside this migration chain, while the other three tables had nothing.
A migration that assumes its own starting state is a migration that only works on the machine it was
written on. Every step here checks the live schema first.

The backfill assigns existing rows to the only household that exists. That is correct for one
household and a GUESS for two, so it refuses to run when it finds more than one rather than silently
handing someone else's statements to whoever came first.

Revision ID: 068
Revises: 067
"""

import sqlalchemy as sa
from alembic import op

revision = "068"
down_revision = "067"
branch_labels = None
depends_on = None

# (table, the single-column primary key that must become composite, or None)
TABLES = (
    ("statements", None),
    ("deleted_fingerprints", "fingerprint"),
    ("evaluated_sms", "fingerprint"),
    ("sync_runs", None),
)


def _has_column(conn, table: str, column: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"), {"t": table, "c": column}).scalar())


def _pk_columns(conn, table: str) -> list[str]:
    return [r[0] for r in conn.execute(sa.text("""
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = CAST(:t AS regclass) AND i.indisprimary
        ORDER BY a.attnum
    """), {"t": table})]


def _has_index(conn, name: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": name}).scalar())


def _has_constraint(conn, name: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": name}).scalar())


def upgrade() -> None:
    conn = op.get_bind()
    households = conn.execute(sa.text("SELECT id FROM households ORDER BY id")).scalars().all()
    if len(households) != 1:
        raise RuntimeError(
            f"expected exactly 1 household to backfill from, found {len(households)}. "
            "These rows carry no tenant, so assigning them automatically would be a guess. "
            "Backfill by hand from each row's related transactions, then re-run."
        )
    hid = households[0]

    for table, pk_col in TABLES:
        if not _has_column(conn, table, "household_id"):
            op.add_column(table, sa.Column("household_id", sa.Integer(), nullable=True))

        # Backfill only the rows that are still missing it, so a re-run costs nothing and a
        # partially-populated column is completed rather than overwritten.
        conn.execute(sa.text(
            f"UPDATE {table} SET household_id = :hid WHERE household_id IS NULL"), {"hid": hid})
        op.alter_column(table, "household_id", nullable=False)

        if pk_col:
            if _pk_columns(conn, table) != ["household_id", pk_col]:
                op.drop_constraint(f"{table}_pkey", table, type_="primary")
                op.create_primary_key(f"{table}_pkey", table, ["household_id", pk_col])
        elif not _has_index(conn, f"ix_{table}_household_id"):
            op.create_index(f"ix_{table}_household_id", table, ["household_id"])

        if not _has_constraint(conn, f"fk_{table}_household"):
            op.create_foreign_key(f"fk_{table}_household", table, "households",
                                  ["household_id"], ["id"])


def downgrade() -> None:
    conn = op.get_bind()
    for table, pk_col in TABLES:
        if _has_constraint(conn, f"fk_{table}_household"):
            op.drop_constraint(f"fk_{table}_household", table, type_="foreignkey")
        if pk_col:
            if _pk_columns(conn, table) == ["household_id", pk_col]:
                op.drop_constraint(f"{table}_pkey", table, type_="primary")
                op.create_primary_key(f"{table}_pkey", table, [pk_col])
        elif _has_index(conn, f"ix_{table}_household_id"):
            op.drop_index(f"ix_{table}_household_id", table_name=table)
        if _has_column(conn, table, "household_id"):
            op.drop_column(table, "household_id")
