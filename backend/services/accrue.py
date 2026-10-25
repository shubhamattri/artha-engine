"""Accrual maths for recurring investments (SIP, RD, EPF, PPF).

One monthly simulation drives all four. Starting from an opening balance known at
`as_of`, each month we: add the recurring contribution (+ employer match), add any
ad-hoc lump sums dated that month, then apply one month of interest. The monthly
rate is derived from the stated compounding frequency so quarterly (RD) and annual
(EPF/PPF) instruments grow correctly even though we step monthly.

Nothing here is fetched or hardcoded per-instrument — value is a pure function of
the inputs, recomputed on every read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


def _monthly_rate(annual_rate_pct: float, compounding: str) -> float:
    """Effective monthly growth factor − 1, equivalent to the stated compounding."""
    r = annual_rate_pct / 100.0
    if r == 0:
        return 0.0
    if compounding == "monthly":
        return r / 12.0
    if compounding == "quarterly":
        return (1 + r / 4.0) ** (1 / 3.0) - 1
    return (1 + r) ** (1 / 12.0) - 1  # annual (default)


def _months(start: date, end: date) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


@dataclass
class AccrualState:
    current_value: float
    total_contributed: float       # principal put in (opening + all contributions)
    interest_earned: float
    months_elapsed: int
    monthly_inflow: float          # what goes in each month right now
    maturity_value: float | None   # projected value at maturity (RD/PPF), else None
    matured: bool


def _simulate(
    opening_balance: float,
    as_of: date,
    until: date,
    monthly: float,
    employer: float,
    monthly_rate: float,
    adhoc: list[dict],
    maturity_date: date | None,
    contribute: bool,
) -> tuple[float, float]:
    """Step month-by-month from `as_of` to `until`. Returns (value, total_contributed)."""
    bal = float(opening_balance)
    contributed = float(opening_balance)
    # index ad-hoc lump sums by (year, month)
    lumps: dict[tuple[int, int], float] = {}
    for c in adhoc or []:
        try:
            d = date.fromisoformat(c["date"])
            lumps[(d.year, d.month)] = lumps.get((d.year, d.month), 0.0) + float(c.get("amount", 0))
        except Exception:
            continue

    n = _months(as_of, until)
    y, m = as_of.year, as_of.month
    for _ in range(n):
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        cur = date(y, m, 1)
        past_maturity = maturity_date is not None and cur > maturity_date
        if contribute and not past_maturity:
            add = monthly + employer
            bal += add
            contributed += add
        lump = lumps.get((y, m), 0.0)
        if lump:
            bal += lump
            contributed += lump
        if not past_maturity:
            bal *= 1 + monthly_rate
    return bal, contributed


def accrue(
    *,
    opening_balance: float,
    as_of: date,
    today: date,
    monthly_contribution: float,
    employer_contribution: float,
    annual_rate_pct: float,
    compounding: str,
    contributions: list[dict] | None,
    maturity_date: date | None,
    active: bool,
) -> AccrualState:
    mr = _monthly_rate(annual_rate_pct, compounding)
    contribute = active

    value, contributed = _simulate(
        opening_balance, as_of, today, monthly_contribution, employer_contribution,
        mr, contributions or [], maturity_date, contribute,
    )

    maturity_value = None
    matured = False
    if maturity_date is not None:
        if today >= maturity_date:
            matured = True
            maturity_value = round(value, 2)
        else:
            mv, _ = _simulate(
                opening_balance, as_of, maturity_date, monthly_contribution,
                employer_contribution, mr, contributions or [], maturity_date, contribute,
            )
            maturity_value = round(mv, 2)

    return AccrualState(
        current_value=round(value, 2),
        total_contributed=round(contributed, 2),
        interest_earned=round(value - contributed, 2),
        months_elapsed=_months(as_of, today),
        monthly_inflow=round((monthly_contribution + employer_contribution) if (active and not matured) else 0.0, 2),
        maturity_value=maturity_value,
        matured=matured,
    )
