"""Which month a household's financial year begins in.

The year view assumed January. In India the financial year runs April to March, so a January-based
year splits every real annual figure across two of them and answers a question nobody asked.

Per household, not global: this is April in India, January in much of Europe, October for the US
federal year. Hardcoding one country's convention into a product meant for many households is the
same mistake as hardcoding a name.

NULL means "the default", which `household_year_start` resolves to April. Existing rows therefore
change behaviour on upgrade, which is intended — nobody had a correct year before this.

Revision ID: 064
Revises: 063
"""

import sqlalchemy as sa
from alembic import op

revision = "064"
down_revision = "063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("households", sa.Column("year_start_month", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("households", "year_start_month")
