"""Money Slice Contract — ONE definition of "what money, which window, which sources" that every tab
shares, so Plan / Spending / Analytics / Cockpit reconcile to the rupee instead of each applying its
own rules.

Axes (locked with the owner 2026-07-03):
  kind        : 'variable_spend' (the 10 everyday categories) | 'all_household_spend' (every SPEND row,
                incl. Rent + Insurance — must be labelled 'All spend' so it's never read as the budget)
  cycle_mode  : 'instrument_cycle' (per-card statement cycle — CANONICAL, matches Plan/Spending)
              | 'household_cycle' (one uniform start-day window) | 'calendar_month'
  sources     : ALL — card AND bank-account SPEND both count. `txn_type=SPEND` already excludes salary,
                EMIs, card-bill payments and transfers, so a bank-account SPEND (e.g. an education fee)
                IS real spend. There is NO `card_type NOT LIKE %account%` exclusion here (that was the
                bug that dropped ₹36k of account spend from Analytics).

Always: direction=debit, txn_type=SPEND, dup_of_id IS NULL, archived=False, household-scoped.

Reconciliation guarantee: for `instrument_cycle`, a transaction is bucketed by how many cycles ago its
own card's cycle is relative to now, then labelled by the household cycle's close-month at that offset.
So EVERY card's current cycle (offset 0) lands in the same 'current' bucket, and that bucket's total
equals Plan's per-card 'this cycle' — regardless of where in the month we are.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from models.transaction import Transaction
from services.categories import VARIABLE_CATEGORIES
from services.cycle import card_start_day, cycle_bounds, in_current_cycle, month_bounds

VARIABLE = {c.value for c in VARIABLE_CATEGORIES}


def _household_start(db: Session | None = None, household_id: int | None = None) -> int:
    """Delegates to the ONE resolver in services.cycle so this module can't drift from it."""
    from services.cycle import household_start_day
    return household_start_day(db, household_id)


def base_rows(db: Session, household_id: int | None) -> list[Transaction]:
    """Every genuine spend debit for the household (all sources, deduped, non-archived).

    household_id=None means "all households" (single-household safe), exactly like
    cycle.current_cycle_rows. The filter MUST be conditional: `household_id == None` compiles to SQL
    `IS NULL`, which matches ZERO real rows (every txn has a real id) — that silently returned an empty
    slice for every None-scoped caller, so money_slice screens showed ₹0 while the card_cycle path
    (which skips the filter on None) showed the real total. That split was a 'wrong data everywhere' bug.
    """
    q = (
        db.query(Transaction)
        .filter(Transaction.direction == "debit")
        .filter(Transaction.txn_type == "SPEND")
        .filter(Transaction.dup_of_id.is_(None))
        .filter(Transaction.archived.is_(False))
    )
    if household_id is not None:
        q = q.filter(Transaction.household_id == household_id)
    return q.all()


def _variable_set(db: Session, household_id: int | None) -> set[str]:
    """The household's everyday-spend categories = the 10 built-ins + this household's custom budget
    categories. Custom categories carry txn_type=SPEND, so they're already in base_rows; this is what
    keeps them INSIDE 'variable_spend' too (else the Plan/Analytics would drop their spend)."""
    from services.categories import variable_category_values
    return set(variable_category_values(db, household_id))


def in_kind(category: str | None, kind: str, variable: set[str] | None = None) -> bool:
    if kind == "variable_spend":
        return category in (variable if variable is not None else VARIABLE)
    return True   # all_household_spend


def _close_month(cend: date) -> str:
    close = cend - timedelta(days=1)
    return f"{close.year:04d}-{close.month:02d}"


def _shift_month_str(month: str, offset: int) -> str:
    y, m = map(int, month.split("-"))
    idx = y * 12 + (m - 1) + offset
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def period_key(txn: Transaction, cycle_mode: str, today: date, hh_start: int,
               db: Session | None = None, household_id: int | None = None) -> str | None:
    """Which period-column this txn belongs to, as a 'YYYY-MM' key (clean shared columns for all modes)."""
    if not txn.date:
        return None
    d = date.fromisoformat(txn.date[:10])
    if cycle_mode == "calendar_month":
        return txn.stmt_month or txn.date[:7]
    if cycle_mode == "household_cycle":
        _, cend = cycle_bounds(d, hh_start)
        return _close_month(cend)
    # instrument_cycle: bucket by the txn's own card-cycle OFFSET from now, labelled by the household
    # cycle's close-month at that offset → every card's current cycle shares the 'current' column.
    sd = card_start_day(txn.bank, txn.card_type, db, household_id)
    tstart, _ = cycle_bounds(d, sd)
    cstart, _ = cycle_bounds(today, sd)
    offset = (tstart.year - cstart.year) * 12 + (tstart.month - cstart.month)
    _, hh_cend = cycle_bounds(today, hh_start)
    return _shift_month_str(_close_month(hh_cend), offset)


def current_key(cycle_mode: str, today: date, hh_start: int) -> str:
    if cycle_mode == "calendar_month":
        return f"{today.year:04d}-{today.month:02d}"
    _, cend = cycle_bounds(today, hh_start)
    return _close_month(cend)


def refunds_by_period(db: Session, household_id: int | None,
                      cycle_mode: str = "instrument_cycle") -> dict[str, float]:
    """Refund credits bucketed by the SAME period windows matrix() uses, so a period's net spend =
    gross − refunds[period]. Refunds net at the period level (a refund can't be reliably attributed to a
    category), which is why the matrix cells stay gross and this is returned alongside them."""
    from models.transaction import Transaction
    today = date.today()
    hh_start = _household_start(db, household_id)
    q = (db.query(Transaction)
         .filter(Transaction.direction == "credit", Transaction.txn_type == "REFUND",
                 Transaction.dup_of_id.is_(None), Transaction.archived.is_(False)))
    if household_id is not None:
        q = q.filter(Transaction.household_id == household_id)
    out: dict[str, float] = {}
    for r in q.all():
        if not r.date:
            continue
        k = period_key(r, cycle_mode, today, hh_start, db, household_id)
        if not k:
            continue
        out[k] = round(out.get(k, 0.0) + (r.amount or 0.0), 2)
    return out


def matrix(db: Session, household_id: int, *, kind: str = "variable_spend",
           cycle_mode: str = "instrument_cycle") -> dict:
    """The canonical period × category matrix. Each cell carries its txn ids so a drill reconciles to
    the rupee. `current` names the live column. `refunds_by_month` lets a caller show NET = gross −
    refunds per period (category cells stay gross)."""
    today = date.today()
    hh_start = _household_start(db, household_id)
    vset = _variable_set(db, household_id)
    per: dict[str, dict] = {}
    grand: dict[str, float] = {}
    for r in base_rows(db, household_id):
        if not in_kind(r.category, kind, vset) or not r.category:
            continue
        k = period_key(r, cycle_mode, today, hh_start, db, household_id)
        if not k:
            continue
        cell = per.setdefault(r.category, {"category": r.category, "byMonth": {},
                                           "idsByMonth": {}, "total": 0.0})
        amt = r.amount or 0.0
        cell["byMonth"][k] = round(cell["byMonth"].get(k, 0.0) + amt, 2)
        cell["idsByMonth"].setdefault(k, []).append(r.id)
        cell["total"] = round(cell["total"] + amt, 2)
        grand[k] = round(grand.get(k, 0.0) + amt, 2)
    months = sorted(grand)
    cats = sorted(per.values(), key=lambda c: -c["total"])
    for c in cats:
        c["avg"] = round(c["total"] / max(1, len(c["byMonth"])), 2)
    return {
        "kind": kind, "cycle_mode": cycle_mode, "months": months,
        "categories": cats, "grand_total_by_month": grand,
        "refunds_by_month": refunds_by_period(db, household_id, cycle_mode),
        "current": current_key(cycle_mode, today, hh_start),
    }


def rows_for_anchor(db: Session, household_id: int, anchor: date, *, kind: str = "variable_spend",
                    cycle_mode: str = "instrument_cycle") -> list[Transaction]:
    """The spend rows in the period CONTAINING `anchor` (a date) — the Plan/Cockpit path (address a
    specific period), consistent with the matrix path (both share base_rows + kind + the cycle windows).
    instrument_cycle uses each card's own window (in_current_cycle); household/calendar use one window."""
    vset = _variable_set(db, household_id)
    rows = [r for r in base_rows(db, household_id) if in_kind(r.category, kind, vset) and r.date]
    if cycle_mode == "instrument_cycle":
        return [r for r in rows
                if in_current_cycle(r.date, r.bank, r.card_type, anchor, db, household_id)]
    if cycle_mode == "household_cycle":
        cs, ce = cycle_bounds(anchor, _household_start(db, household_id))
    else:  # calendar_month
        first, last = month_bounds(anchor)
        cs, ce = first, last + timedelta(days=1)
    lo, hi = cs.isoformat(), ce.isoformat()
    return [r for r in rows if lo <= r.date[:10] < hi]


def period_total(db: Session, household_id: int, *, period: str, kind: str = "variable_spend",
                 cycle_mode: str = "instrument_cycle") -> tuple[float, list[int]]:
    """Total + ids for one period bucket of a slice (the reconciliation primitive)."""
    today = date.today()
    hh_start = _household_start(db, household_id)
    vset = _variable_set(db, household_id)
    tot = 0.0
    ids: list[int] = []
    for r in base_rows(db, household_id):
        if not in_kind(r.category, kind, vset) or not r.category:
            continue
        if period_key(r, cycle_mode, today, hh_start, db, household_id) == period:
            tot += r.amount or 0.0
            ids.append(r.id)
    return round(tot, 2), ids
