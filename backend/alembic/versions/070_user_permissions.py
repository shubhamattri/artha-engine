"""Per-user permission grants.

One row per (user, key) that a MEMBER holds. Elevated roles need no rows — the resolver gives them
`edit` everywhere and ignores grants entirely, so an admin's rows would be dead weight rather than a
second source of truth to keep in step.

Absence means `none`. Writing a row per key per user so every combination is explicit sounds tidier
until a key is added to the catalogue and every existing person has no row for it, at which point
the code has to decide what a missing row means anyway. Decided once, here, meaning the safe thing.

No backfill. The only member-role users today are ones nobody has granted anything to, and giving
them access as a side effect of a migration is precisely the accident this layer exists to prevent.

Revision ID: 070
Revises: 069
"""

import sqlalchemy as sa
from alembic import op

revision = "070"
down_revision = "069"
branch_labels = None
depends_on = None


def _has_table(conn, name: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = :n"), {"n": name}).scalar())


def upgrade() -> None:
    if _has_table(op.get_bind(), "user_permissions"):
        return

    op.create_table(
        "user_permissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        # Text rather than an enum: removing a key from the catalogue must not make existing rows
        # unreadable. The resolver ignores keys it does not recognise.
        sa.Column("key", sa.String(40), nullable=False),
        # auth.roles.Level as an int — 0 none, 1 view, 2 edit — ordered so `granted >= required` is
        # the whole comparison.
        sa.Column("level", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("granted_at", sa.DateTime(), nullable=True),
        sa.Column("granted_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint("user_id", "key", name="uq_user_permission_key"),
    )
    op.create_index("ix_user_permissions_user_id", "user_permissions", ["user_id"])


def downgrade() -> None:
    if _has_table(op.get_bind(), "user_permissions"):
        op.drop_index("ix_user_permissions_user_id", table_name="user_permissions")
        op.drop_table("user_permissions")
