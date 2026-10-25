"""advisor_chat + advisor_message — append-only, multi-tenant advisor conversation store.

Two new tables underpin the grounded advisor (T4), the feedback loop (T2), the number
verifier (T7), and the weekly coach (T9).  Nothing can learn without stored chats —
this migration is the foundation everything else builds on.

advisor_chat  — one conversation (household_id, lens, status, title)
advisor_message — one turn: user question OR assistant answer, with cited_numbers +
                  tools_called (provenance) + feedback columns (training signal).

Revision ID: 033
Revises: 032
"""
import sqlalchemy as sa
from alembic import op

revision = "033"; down_revision = "032"; branch_labels = None; depends_on = None


def upgrade() -> None:
    # advisor_chat must be created first — advisor_message has a FK to it.
    op.create_table(
        "advisor_chat",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # Tenant key — identical pattern to transactions.household_id.
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"),
                  nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        # 'cycle' | 'month' — the PeriodToggle lens at chat start.
        sa.Column("lens", sa.String(8), nullable=False, server_default="cycle"),
        # 'active' | 'archived'
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        # Optional first-message summary; NOT PII-sanitised at write.
        sa.Column("title", sa.String(), nullable=True),
    )
    op.create_index("ix_advisor_chat_household_id", "advisor_chat", ["household_id"])

    op.create_table(
        "advisor_message",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.Integer(), sa.ForeignKey("advisor_chat.id"),
                  nullable=False),
        # 0-based ordinal within the chat; enforced unique below.
        sa.Column("seq", sa.Integer(), nullable=False),
        # 'user' | 'assistant'
        sa.Column("role", sa.String(9), nullable=False),
        # Final prose answer / user question.  Text = unlimited (Opus can be verbose).
        sa.Column("content", sa.Text(), nullable=False),
        # Assistant-only provenance —- NULL on user rows.
        sa.Column("cited_numbers", sa.JSON(), nullable=True),
        sa.Column("tools_called", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        # Feedback columns — NULL until the user rates.
        sa.Column("feedback_rating", sa.String(4), nullable=True),
        sa.Column("feedback_note", sa.String(), nullable=True),
        sa.Column("feedback_at", sa.DateTime(), nullable=True),
        # Uniqueness guarantee: no two messages share the same seq within a chat.
        sa.UniqueConstraint("chat_id", "seq", name="uq_advisor_message_seq"),
    )
    op.create_index("ix_advisor_message_chat_id", "advisor_message", ["chat_id"])


def downgrade() -> None:
    # Drop child table before parent to respect the FK constraint.
    op.drop_index("ix_advisor_message_chat_id", table_name="advisor_message")
    op.drop_table("advisor_message")
    op.drop_index("ix_advisor_chat_household_id", table_name="advisor_chat")
    op.drop_table("advisor_chat")
