"""insurance policies (term / TROP / endowment / ulip / health)

Revision ID: 015
Revises: 014
"""
import sqlalchemy as sa
from alembic import op
revision = "015"; down_revision = "014"; branch_labels = None; depends_on = None

def upgrade() -> None:
    op.create_table("insurance_policies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("plan_name", sa.String(), nullable=True),
        sa.Column("policy_no", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False, server_default="term"),
        sa.Column("holder", sa.String(), nullable=True),
        sa.Column("life_insured", sa.String(), nullable=True),
        sa.Column("sum_assured", sa.Float(), nullable=False, server_default="0"),
        sa.Column("premium", sa.Float(), nullable=False, server_default="0"),
        sa.Column("premium_frequency", sa.String(), nullable=False, server_default="monthly"),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("policy_term_years", sa.Integer(), nullable=True),
        sa.Column("premium_paying_term_years", sa.Integer(), nullable=True),
        sa.Column("return_of_premium", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("maturity_date", sa.Date(), nullable=True),
        sa.Column("surrender_value", sa.Float(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True))

def downgrade() -> None:
    op.drop_table("insurance_policies")
