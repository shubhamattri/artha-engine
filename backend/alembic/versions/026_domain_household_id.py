"""household_id on every remaining domain table — the tenancy foundation

Slices 1–4 made `transactions` + `users` multi-tenant. This adds the same tenant key to all the other
domain tables and backfills them to the first household, so the per-household scoping of their
endpoints (the next slices) is pure query logic. Additive + server_default=1, so existing ORM inserts
that don't yet pass household_id still get a valid value (the single household today). Models gain the
mapped column when each table's endpoints are scoped.

Revision ID: 026
Revises: 025
"""
import sqlalchemy as sa
from alembic import op

revision = "026"; down_revision = "025"; branch_labels = None; depends_on = None

# every domain table that holds household-owned financial data (deleted_fingerprints = global
# tombstones, password_reset_tokens = user-scoped → intentionally excluded)
TABLES = [
    "loans", "cashflow_lines", "budgets", "budget_categories", "assets",
    "insurance_policies", "recurring_investments", "sinking_funds", "statements", "category_rules",
]


def upgrade() -> None:
    for t in TABLES:
        op.add_column(t, sa.Column("household_id", sa.Integer(), nullable=True))
        op.execute(
            f"UPDATE {t} SET household_id = "
            f"(SELECT id FROM households ORDER BY id LIMIT 1) WHERE household_id IS NULL"
        )
        op.alter_column(t, "household_id", nullable=False, server_default="1")
        op.create_index(f"ix_{t}_household_id", t, ["household_id"])
        op.create_foreign_key(f"fk_{t}_household_id", t, "households", ["household_id"], ["id"])


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_constraint(f"fk_{t}_household_id", t, type_="foreignkey")
        op.drop_index(f"ix_{t}_household_id", table_name=t)
        op.drop_column(t, "household_id")
