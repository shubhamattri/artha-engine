"""Roles on users, and exactly one super admin per household.

The uniqueness is a partial index rather than an application check on purpose: a check in Python
loses races and is bypassed by `scripts/seed_users.py`, `scripts/invite_user.py` and anything else
that writes the table directly. The database is the only place the rule cannot be routed around.

The backfill needs a decision it must not make for itself: which existing user holds super admin.
It reads ARTHA_SUPER_ADMIN_USERNAME and fails loudly when that is absent, rather than picking the
lowest id and quietly handing the keys to whoever signed up first.

Written to run against a schema that may already have the column — the same lesson as 068, which
died on the server assuming a pristine starting state.

Revision ID: 069
Revises: 068
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "069"
down_revision = "068"
branch_labels = None
depends_on = None

INDEX = "uq_one_super_admin_per_household"


def _has_column(conn, table: str, column: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"), {"t": table, "c": column}).scalar())


def _has_index(conn, name: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": name}).scalar())


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_column(conn, "users", "role"):
        op.add_column("users", sa.Column("role", sa.String(20), nullable=False,
                                         server_default="member"))

    if not _has_index(conn, INDEX):
        op.create_index(INDEX, "users", ["household_id"], unique=True,
                        postgresql_where=sa.text("role = 'super_admin'"))

    # Idempotent: if a super admin already exists, leave it and require no environment variable.
    existing = conn.execute(sa.text(
        "SELECT username FROM users WHERE role = 'super_admin'")).scalars().all()
    if existing:
        return
    # A fresh install has no users to backfill; the first one is created with its role set.
    if not conn.execute(sa.text("SELECT 1 FROM users LIMIT 1")).scalar():
        return

    username = os.environ.get("ARTHA_SUPER_ADMIN_USERNAME")
    if not username:
        raise RuntimeError(
            "set ARTHA_SUPER_ADMIN_USERNAME to the username that should hold super admin. "
            "Refusing to guess: this decides who can grant every other permission in the household."
        )
    res = conn.execute(sa.text(
        "UPDATE users SET role = 'super_admin' WHERE username = :u"), {"u": username})
    if res.rowcount != 1:
        raise RuntimeError(
            f"no user named {username!r} — nothing was changed. Check the username and re-run."
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _has_index(conn, INDEX):
        op.drop_index(INDEX, table_name="users")
    if _has_column(conn, "users", "role"):
        op.drop_column("users", "role")
