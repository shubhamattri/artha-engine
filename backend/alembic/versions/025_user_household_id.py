"""users.household_id — tenancy resolves from the logged-in user, not "the first household"

Slice 1 scoped transactions by household; this links each USER to a household so
get_current_household can resolve per-user (the real multi-tenant unlock). Backfills existing users
to the first household.

Revision ID: 025
Revises: 024
"""
import sqlalchemy as sa
from alembic import op

revision = "025"; down_revision = "024"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("household_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE users SET household_id = "
        "(SELECT id FROM households ORDER BY id LIMIT 1) WHERE household_id IS NULL"
    )
    op.alter_column("users", "household_id", nullable=False, server_default="1")
    op.create_index("ix_users_household_id", "users", ["household_id"])
    op.create_foreign_key("fk_users_household_id", "users", "households", ["household_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_users_household_id", "users", type_="foreignkey")
    op.drop_index("ix_users_household_id", table_name="users")
    op.drop_column("users", "household_id")
