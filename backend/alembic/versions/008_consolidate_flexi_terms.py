"""Consolidate the 4 flexi-only columns into a single JSON flexi_terms column.

Standard loans no longer carry empty disbursed_amount / disbursements /
moratorium_months / moratorium_interest_mode columns — those only ever applied
to flexi loans. Flexi config now lives together in one nullable JSON blob.

Revision ID: 008
Revises: 007
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

_OLD_COLS = ("disbursed_amount", "disbursements", "moratorium_months", "moratorium_interest_mode")


def upgrade() -> None:
    op.add_column("loans", sa.Column("flexi_terms", sa.JSON(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, disbursed_amount, disbursements, moratorium_months, "
        "moratorium_interest_mode FROM loans WHERE loan_type = 'flexi'"
    )).fetchall()
    for r in rows:
        terms = {
            "disbursed_amount": r.disbursed_amount,
            "disbursements": r.disbursements or [],
            "moratorium_months": r.moratorium_months or 0,
            "interest_mode": r.moratorium_interest_mode or "full",
        }
        conn.execute(
            sa.text("UPDATE loans SET flexi_terms = CAST(:t AS json) WHERE id = :i"),
            {"t": json.dumps(terms), "i": r.id},
        )
    for c in _OLD_COLS:
        op.drop_column("loans", c)


def downgrade() -> None:
    op.add_column("loans", sa.Column("disbursed_amount", sa.Float(), nullable=True))
    op.add_column("loans", sa.Column("disbursements", sa.JSON(), nullable=True))
    op.add_column("loans", sa.Column("moratorium_months", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("loans", sa.Column("moratorium_interest_mode", sa.String(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, flexi_terms FROM loans WHERE flexi_terms IS NOT NULL"
    )).fetchall()
    for r in rows:
        ft = r.flexi_terms if isinstance(r.flexi_terms, dict) else json.loads(r.flexi_terms or "{}")
        conn.execute(sa.text(
            "UPDATE loans SET disbursed_amount = :d, disbursements = CAST(:tr AS json), "
            "moratorium_months = :m, moratorium_interest_mode = :mode WHERE id = :i"
        ), {
            "d": ft.get("disbursed_amount"),
            "tr": json.dumps(ft.get("disbursements") or []),
            "m": ft.get("moratorium_months") or 0,
            "mode": ft.get("interest_mode"),
            "i": r.id,
        })
    op.drop_column("loans", "flexi_terms")
