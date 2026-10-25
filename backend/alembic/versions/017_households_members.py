"""households + members — multi-tenant core; seed default household + members from holders

Revision ID: 017
Revises: 016
"""
import sqlalchemy as sa
from alembic import op
revision="017"; down_revision="016"; branch_labels=None; depends_on=None

def upgrade() -> None:
    op.create_table(
        "households",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False, server_default="My Household"),
        sa.Column("currency", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
    )
    op.create_table(
        "members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("is_household", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
    )
    # Seed the first household and derive its members from every existing free-text `holder`.
    op.execute("INSERT INTO households (name) VALUES ('My Household')")
    op.execute("""
        INSERT INTO members (household_id, name, key, is_household, active, created_at)
        SELECT (SELECT id FROM households ORDER BY id LIMIT 1),
               h, h, (h = 'Household'), true, now()
        FROM (
            SELECT DISTINCT holder AS h FROM (
                SELECT holder FROM assets
                UNION SELECT holder FROM loans
                UNION SELECT holder FROM cashflow_lines
                UNION SELECT holder FROM recurring_investments
                UNION SELECT holder FROM sinking_funds
                UNION SELECT holder FROM budget_categories
                UNION SELECT holder FROM insurance_policies
            ) u WHERE holder IS NOT NULL AND holder <> ''
        ) d
    """)

def downgrade() -> None:
    op.drop_table("members")
    op.drop_table("households")
