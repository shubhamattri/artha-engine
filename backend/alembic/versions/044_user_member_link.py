"""Join a login to the person it belongs to.

A human was represented twice with nothing connecting them: a `users` row that can sign in, and a
`members` row that holds cards, a holder name and the identity banks need. Nothing joined the two, so
the profile screen had to guess which member the signed-in user was, and onboarding wrote to a record
the user had no direct claim on.

Backfills by matching name to the member key or name, which is exactly how holder strings have always
been matched elsewhere. A user with no obvious match is simply left unlinked rather than guessed at.

Revision ID: 044
Revises: 043
"""

import sqlalchemy as sa
from alembic import op

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("member_id", sa.Integer(), nullable=True))
    op.create_index("ix_users_member_id", "users", ["member_id"])
    op.create_foreign_key("fk_users_member", "users", "members", ["member_id"], ["id"])

    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE users u SET member_id = m.id
        FROM members m
        WHERE m.household_id = u.household_id
          AND m.is_household = false
          AND (lower(m.key) = lower(u.name) OR lower(m.name) = lower(u.name))
          AND u.member_id IS NULL
    """))


def downgrade() -> None:
    op.drop_constraint("fk_users_member", "users", type_="foreignkey")
    op.drop_index("ix_users_member_id", table_name="users")
    op.drop_column("users", "member_id")
