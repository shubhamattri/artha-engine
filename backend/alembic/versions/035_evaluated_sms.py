"""evaluated_sms — remembers SMS the LLM already judged non-spend, so the 10-min sync stops
re-classifying the same 30-day backlog of OTP/promo/reminder messages on every run.

Revision ID: 035
Revises: 034
"""
import sqlalchemy as sa
from alembic import op

revision = "035"; down_revision = "034"; branch_labels = None; depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluated_sms",
        sa.Column("fingerprint", sa.String(40), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("evaluated_sms")
