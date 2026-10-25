"""The year: does the annual envelope hold, and what actually consumed it.

The envelope is the twelve monthly targets added up. **Monthly targets never move.** A month that
came in under does not raise the next month's allowance; this module only reports whether the sum
holds, and it never writes a Budget row.

WHY THERE IS NO "EVERYDAY vs ONE-OFF" SPLIT HERE, having built one and thrown it away:

A car and a coffee should not be judged by the same yardstick, so the obvious design was to divide
spend into the everyday habit and one-off purchases, then compare only the habit to the target.
`services/projection.py` already draws that line by transaction SIZE, and reusing it looked free.

It was not. Sweeping the size threshold across a wide range moved "everyday spend" several-fold and
the month-to-month spread never settled at any setting: real spending is lumpy at every scale, so
there is no value where habit separates from event. The verdict flipped between "on plan" and well
over purely by moving a constant nobody on the screen can see. A number that answers whatever you configure it to
answer is not a measurement.

So the year is reported without any threshold at all:

  * the total, against the envelope — a fact
  * the exact date the envelope ran out — a fact, found by walking the ledger in date order
  * the biggest single purchases, BY NAME — a household recognises its own car dealer or jeweller
    in a way no statistic conveys, and the UI lets them exclude the ones they consider exceptional
    and watch what the rest costs per month

That last move is the point: rather than the app deciding what counts as exceptional with a hidden
constant, the household draws its own line on named rows, and the arithmetic follows.

`api/budget` OWNS actual spend, so every monthly figure here comes from `card_cycle_spend` and must
equal what the Plan shows for the same period. `services/consistency.py` asserts it.
"""

from __future__ import annotations

from datetime import date

from api.deps import get_current_household
from database import get_db
from fastapi import APIRouter, Depends, Query
from models.household import Budget
from models.tenant import Household
from sqlalchemy.orm import Session

router = APIRouter()

# Enough named rows for a household to find the ones it considers exceptional, without turning the
# screen into the transaction list it already has on another tab.
TOP_PURCHASES = 20

# India's financial year. The fallback, never the assumption — `Household.year_start_month` decides,
# and a household filing on a January or October year gets its own.
DEFAULT_YEAR_START_MONTH = 4


def household_year_start(db: Session, household_id: int | None) -> int:
    """The month this household's financial year opens in, 1–12."""
    from models.tenant import Household
    if household_id is None:
        return DEFAULT_YEAR_START_MONTH
    row = db.query(Household.year_start_month).filter(Household.id == household_id).first()
    raw = row[0] if row else None
    return raw if raw and 1 <= raw <= 12 else DEFAULT_YEAR_START_MONTH


def year_containing(day: date, start_month: int) -> int:
    """Which financial year a date falls in, named by the year it OPENS in. On an April year, 18 Aug
    2026 is in 2026 and 5 Feb 2026 is in 2025 — the January-to-March tail belongs to the year before
    it, which is the whole reason a January-based view split every annual figure in two."""
    return day.year if day.month >= start_month else day.year - 1


def year_label(year: int, start_month: int) -> str:
    """'2026' when the year is the calendar one, '2026–27' when it straddles two."""
    return str(year) if start_month == 1 else f"{year}–{str(year + 1)[2:]}"


def _months_of(year: int, start_month: int) -> list[str]:
    out, y, m = [], year, start_month
    for _ in range(12):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _month_targets(db: Session, household_id: int | None, months: list[str]) -> dict[str, float]:
    """Each month's total target, read the way the Plan reads it: a month with no rows of its own
    inherits the most recent month that has them. A future month therefore shows the CURRENT plan
    projected forward, which is a forecast, not a budget anyone has set."""
    q = db.query(Budget)
    if household_id is not None:
        q = q.filter(Budget.household_id == household_id)
    by_month: dict[str, float] = {}
    for row in q.all():
        by_month[row.month] = round(by_month.get(row.month, 0.0) + (row.monthly_limit_inr or 0.0), 2)

    out: dict[str, float] = {}
    for m in months:
        if m in by_month:
            out[m] = by_month[m]
            continue
        earlier = [k for k in by_month if k < m]
        out[m] = by_month[max(earlier)] if earlier else 0.0
    return out


def _row(r, month: str) -> dict:
    return {"id": r.id, "month": month, "date": (r.date or "")[:10], "category": r.category,
            "description": r.description, "merchant": r.merchant,
            "amount_inr": round(r.amount or 0.0, 2)}


def compute_year(year: int | None, db: Session, household_id: int | None = None,
                 mode: str = "cycle", today: date | None = None) -> dict:
    from api.budget import cycle_anchor, settings_cycle_start
    from services.cycle import (
        card_cycle_spend,
        current_cycle_refunds,
        current_cycle_rows,
        cycle_bounds,
        month_bounds,
        plan_categories,
    )

    today = today or date.today()
    start_month = household_year_start(db, household_id)
    # `year is None` means "the one we are in", which on an April year is NOT today's calendar year
    # for three months of every year. Resolving it here rather than at each caller keeps the
    # endpoint, the invariant and the tests on one definition of "this year".
    year = year if year is not None else year_containing(today, start_month)
    months = _months_of(year, start_month)
    targets = _month_targets(db, household_id, months)
    cats = plan_categories(db, household_id)
    start_day = settings_cycle_start(db, household_id)

    out_months, purchases, cumulative = [], [], 0.0
    for m in months:
        target = round(targets.get(m, 0.0), 2)
        # Every period is addressed the same way, including the live one. `anchor_for` short-circuits
        # the CURRENT calendar month to today, which lands inside the cycle labelled by the NEXT
        # month — harmless when the Plan shows one month, but in a twelve-period sequence it both
        # duplicates a label and drops a whole cycle out of the year. One entire cycle of spend
        # disappeared from the annual total that way, and the total looked normal without it.
        if mode == "month":
            # From `m`, never the loop's `year`: an April year runs into the NEXT calendar year, so
            # its last three periods are 2027-01..03 and dating them from `year` would silently
            # report January 2026 three times.
            first, last = month_bounds(date(int(m[:4]), int(m[5:]), 1))
        else:
            last = cycle_anchor(m, start_day)
            first, _end = cycle_bounds(last, start_day)
        window = {"start": first.isoformat(), "end": last.isoformat()}

        if first > today:                      # the period has not begun; there is nothing to report
            out_months.append({"month": m, "target_inr": target, "net_spent_inr": None,
                               "cumulative_inr": None, "window": window, "state": "future"})
            continue
        # A live period is computed as-of TODAY, so it never counts days that have not happened.
        is_current = last >= today
        asof = today if is_current else last

        net = card_cycle_spend(db, asof, mode=mode, household_id=household_id)
        cumulative = round(cumulative + net, 2)
        for r in current_cycle_rows(db, asof, mode=mode, categories=cats, household_id=household_id):
            purchases.append(_row(r, m))
        out_months.append({
            "month": m,
            "target_inr": target,
            "net_spent_inr": net,
            # "How much was inside the budget and how much was over it" needs NO threshold: it is
            # min and max against that month's own target, and the two always sum back to the
            # month's spend. This is the split the everyday/one-off attempt was reaching for, and
            # it is exact where that one was a setting. Judged per MONTH, because a year-level
            # min/max would let an under month silently absorb an over one, which is precisely the
            # thing monthly targets exist to stop.
            "within_target_inr": round(min(net, target), 2) if target else 0.0,
            "above_target_inr": round(max(0.0, net - target), 2) if target else round(net, 2),
            "cumulative_inr": cumulative,
            "refunds_inr": current_cycle_refunds(db, asof, mode=mode, household_id=household_id),
            "window": window,
            # Load-bearing for the reader: a month at 40% of its target is only good news once the
            # month is over, so the bar for it is drawn differently.
            "state": "partial" if is_current else "closed",
        })

    seen = [r for r in out_months if r["state"] in ("closed", "partial")]
    closed = [r for r in out_months if r["state"] == "closed"]
    envelope = round(sum(r["target_inr"] for r in out_months), 2)
    spent = round(sum(r["net_spent_inr"] for r in seen), 2)

    # Only CLOSED periods set the rate. Including a current period that is three days old reports
    # every breach as averted right up until the moment it lands.
    rate = round(sum(r["net_spent_inr"] for r in closed) / len(closed), 2) if closed else 0.0
    remaining = 12 - len(closed)

    purchases.sort(key=lambda p: -p["amount_inr"])
    ordered = sorted(purchases, key=lambda p: p["date"])

    return {
        "year": year,
        "label": year_label(year, start_month),
        "start_month": start_month,
        "mode": mode,
        "envelope_inr": envelope,
        "net_spent_inr": spent,
        "within_target_inr": round(sum(r["within_target_inr"] for r in seen), 2),
        "above_target_inr": round(sum(r["above_target_inr"] for r in seen), 2),
        # Periods that HAPPENED with no target set. Their spend has no line to be inside of, so all
        # of it lands in "over" — correct, and badly misleading if left unsaid. An earlier year can
        # show a catastrophic overshoot whose real cause is that nobody had started budgeting yet,
        # and the reader has no way to tell that apart from a year that was genuinely blown.
        "unbudgeted_periods": len([r for r in seen if not r["target_inr"]]),
        "purchase_count": len(purchases),
        "months": out_months,
        "top_purchases": purchases[:TOP_PURCHASES],
        "exhausted": _exhausted(ordered, envelope),
        "outlook": {
            "months_closed": len(closed),
            "months_remaining": remaining,
            "rate_inr": rate,
            "year_end_inr": round(sum(r["net_spent_inr"] for r in closed) + rate * remaining, 2),
            # Negative means the envelope is already gone: no amount of restraint over what is left
            # of the year brings the total back under. That IS the answer, so it is published as a
            # number rather than hidden behind a boolean.
            "needed_per_month_inr": round((envelope - spent) / remaining, 2) if remaining else None,
            "breach": bool(envelope) and spent > envelope,
        },
    }


def _exhausted(ordered: list[dict], envelope: float) -> dict | None:
    """The transaction that took the running total past a whole year of target.

    Gross, in date order, deliberately: this answers "when did it run out", and a refund arriving in
    May does not un-spend a purchase made in February.
    """
    if not envelope:
        return None
    run = 0.0
    for p in ordered:
        prev = run
        run += p["amount_inr"]
        if prev < envelope <= run:
            return p
    return None


@router.get("/annual")
def annual(year: int | None = Query(None, description="Financial year, named by the year it opens "
                                                     "in. Defaults to the one containing today."),
           mode: str = Query("cycle", pattern="^(cycle|month)$"),
           db: Session = Depends(get_db),
           household: Household = Depends(get_current_household)) -> dict:
    return compute_year(year, db, household.id, mode=mode)
