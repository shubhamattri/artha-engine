"""Assemble the three household facts the tax engine needs, from the database.

Kept apart from services/tax.py so that module stays pure arithmetic with no DB dependency — it is
the piece worth unit-testing exhaustively, and it should be usable on hypothetical loans too.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from services.tax import REGIME_NEW


def household_tax_context(
    db: Session, household_id: int | None
) -> tuple[str, dict[str, float], set[str]]:
    """(regime, annual income per holder, holders whose property is let out).

    Income is per HOLDER, not per household, because a deduction lands on the borrower's own return
    and is worth their marginal rate. A loan held by a non-earning member carries no relief at all,
    which the engine must see rather than assume the household's top rate.
    """
    from models.household import CashflowLine
    from models.tenant import Household

    hh = db.query(Household).filter(Household.id == household_id).first() if household_id else None
    regime = (getattr(hh, "tax_regime", None) or REGIME_NEW).lower()
    let_out = set(getattr(hh, "let_out_members", None) or [])

    q = db.query(CashflowLine).filter(CashflowLine.kind == "income",
                                      CashflowLine.active.is_(True))
    if household_id is not None:
        q = q.filter(CashflowLine.household_id == household_id)

    income: dict[str, float] = {}
    for line in q.all():
        holder = line.holder or "Household"
        income[holder] = income.get(holder, 0.0) + (line.amount_inr or 0.0) * 12.0
    return regime, income, let_out
