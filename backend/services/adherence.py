"""Did the extra payments the plan assumed actually happen?

The payoff plan projects a date from a monthly figure the household is supposed to put at its loans
on top of every EMI. Rendered as "all loans paid off by March 2031" that reads as a fact. It is a
conditional, and if the money does not go in, the date silently stops being true while the screen
keeps showing it.

The ledger already records what was really paid. Nothing compared the two, so the plan could not
tell the difference between a household following it and one ignoring it.

This measures actual extra payments over recent whole months and reports them against the planned
figure. It never guesses: a month with no ledger row is a month with no extra payment, which is the
honest reading, because the ledger is the only place an extra payment can be recorded.
"""

from __future__ import annotations

from datetime import date

from models.loan_payment import LoanPayment
from sqlalchemy.orm import Session


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months_back(today: date, n: int) -> list[str]:
    """The n whole months before this one, oldest first. The CURRENT month is added separately by
    `measure` and marked partial — it must be visible, because leaving it out hid the month a
    household actually started paying, but it must not be scored against a whole month's plan."""
    keys, y, m = [], today.year, today.month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        keys.append(f"{y:04d}-{m:02d}")
    return list(reversed(keys))


def measure(db: Session, household_id: int, planned_extra: float,
            months: int = 3, today: date | None = None) -> dict:
    """How much of the planned extra actually landed, per month, over `months` whole months plus the
    current month to date.

    The verdict is taken from the WHOLE months. The current one is reported, never scored: a
    fortnight measured against a full month's plan reads as a shortfall for every household on the
    2nd. What the current month DOES decide is whether a verdict is possible at all — a household
    whose first extra payment is this month has not yet failed at anything, and saying "off plan,
    little or no extra is going in" in the month they began succeeding is simply wrong.
    """
    today = today or date.today()
    whole = _months_back(today, months)
    current = _month_key(today)
    if not whole or planned_extra <= 0:
        return {"months": [], "planned_per_month": round(planned_extra, 2), "actual_avg": None,
                "adherence_pct": None, "status": "unknown",
                "why": "no plan figure to measure against"}

    rows = db.query(LoanPayment).filter(LoanPayment.household_id == household_id).all()
    paid: dict[str, float] = dict.fromkeys([*whole, current], 0.0)
    for r in rows:
        k = _month_key(r.date)
        if k in paid and (r.kind or "extra") != "skipped_emi":
            paid[k] += r.amount or 0.0

    actual_avg = sum(paid[k] for k in whole) / len(whole)
    pct = actual_avg / planned_extra * 100.0
    # Bands, not a hard pass/fail: a household that puts in most of the plan most months is
    # following it, and saying otherwise trains people to ignore the warning.
    if pct >= 90:
        status, why = "on_plan", "extra payments are landing close to the plan"
    elif pct >= 40:
        status, why = "behind", "some extra is going in, but well under what the dates assume"
    else:
        status, why = "off_plan", "little or no extra is going in, so the projected dates will slip"
    # The one case the bands get wrong: nothing in any whole month, but money going in RIGHT NOW.
    # There is no habit to judge yet, so the honest answer is that it cannot be judged.
    if paid[current] > 0 and not any(paid[k] > 0 for k in whole):
        status = "too_early"
        why = (f"the extra payments here started this month — {round(paid[current]):,} in so far "
               f"against a plan of {round(planned_extra):,} a month. One part-month is not a habit, "
               f"so there is nothing to judge the dates against yet")
    return {
        "months": [{"month": k, "paid": round(paid[k], 2), "partial": k == current}
                   for k in [*whole, current]],
        "planned_per_month": round(planned_extra, 2),
        # A WHOLE-month rate, so the payoff plan can be re-run at it. A part-month never dilutes it.
        "actual_avg": round(actual_avg, 2),
        "adherence_pct": round(pct, 1),
        "status": status,
        "why": why,
    }
