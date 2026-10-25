from datetime import date

from api.deps import get_current_household
from database import get_db
from fastapi import APIRouter, Body, Depends
from models.household import Budget
from models.tenant import Household
from models.transaction import Transaction
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter()

# The Plan tracks exactly the 10 everyday spend categories — nothing leaks, nothing extra.
# (Committed/movement categories — EMIs, payments, transfers, insurance, fees — live elsewhere.)
from services.categories import VARIABLE_CATEGORIES, custom_category_names, variable_category_values

PLAN_CATEGORIES = [c.value for c in VARIABLE_CATEGORIES]
DEFAULT_MONTH = "2026-06"


class BudgetItemIn(BaseModel):
    category: str
    monthly_limit_inr: float


class BudgetSaveIn(BaseModel):
    month: str
    items: list[BudgetItemIn]


def _resolve_month(month: str | None, db: Session) -> str:
    if month:
        return month
    latest = (
        db.query(Transaction.stmt_month)
        .order_by(Transaction.stmt_month.desc())
        .first()
    )
    if latest and latest[0]:
        return latest[0]
    return DEFAULT_MONTH


def cycle_anchor(month: str, start_day: int) -> "date":
    """Last day of the billing cycle labelled `month`.

    A cycle is labelled by the month it ENDS in, which is what money_slice.current_key returns and
    what everything downstream keys off. Pure and separately testable, because the bug it fixes only
    appears at a rollover and the caller short-circuits on the current month, which made it
    unreachable from a test.
    """
    from calendar import monthrange
    from datetime import date, timedelta
    y, m = map(int, month.split("-"))
    return date(y, m, min(start_day, monthrange(y, m)[1])) - timedelta(days=1)


def anchor_for(month: str, mode: str, db=None, household_id: int | None = None) -> tuple["date", bool]:
    """The date to compute a period AS-OF, plus whether it's the live one. The LENS decides the window:
      • current month → TODAY (partial-period pace stays honest), either lens;
      • past + month lens → the month's LAST day (a completed calendar month, all weeks past);
      • past + cycle lens → the LAST day of that month's billing CYCLE, so you browse previous CYCLES
        (cycle-by-cycle), not a calendar month. This makes 'This cycle | By month' work for history in
        both lenses instead of silently falling back to calendar."""
    from datetime import date, timedelta
    today = date.today()
    if month == today.strftime("%Y-%m"):
        return today, True
    y, m = map(int, month.split("-"))
    if mode == "cycle":
        return cycle_anchor(month, settings_cycle_start(db, household_id)), False
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return nxt - timedelta(days=1), False


def _slice_rows(db: Session, mode: str, anchor: "date", household_id: int | None):
    # THE single spend definition — Plan reads the same Money Slice as Analytics (variable_spend, all
    # sources), so their numbers can't drift. mode 'cycle' → per-card instrument cycle; 'month' → calendar.
    from services import money_slice
    cm = "calendar_month" if mode == "month" else "instrument_cycle"
    return money_slice.rows_for_anchor(db, household_id, anchor, kind="variable_spend", cycle_mode=cm)


def _spend_by_category(db: Session, mode: str, anchor: "date",
                       household_id: int | None = None) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in _slice_rows(db, mode, anchor, household_id):
        out[r.category] = round(out.get(r.category, 0.0) + (r.amount or 0.0), 2)
    return out


def _spend_ids_by_category(db: Session, mode: str, anchor: "date",
                           household_id: int | None = None) -> dict[str, list[int]]:
    # The exact txn ids behind each category's spend this period → the drill fetches by these ids so its
    # total always reconciles with the row, in any lens (cycle/month, current/past).
    out: dict[str, list[int]] = {}
    for r in _slice_rows(db, mode, anchor, household_id):
        out.setdefault(r.category, []).append(r.id)
    return out


def _period_from_anchor(anchor_iso: str, mode: str, db=None,
                        household_id: int | None = None) -> tuple["date", bool, str]:
    """Given an anchor DATE and the lens, the period is the cycle (or calendar month) CONTAINING that
    date — unambiguous, no calendar-month/cycle overlap. Returns (as-of date, is-live, target-month).
    Stepping the anchor by ±1 month walks distinct previous/next periods with no dead step."""
    from datetime import date, timedelta

    from services.cycle import cycle_bounds, month_bounds
    a = date.fromisoformat(anchor_iso)
    today = date.today()
    if mode == "cycle":
        cstart, cend = cycle_bounds(a, settings_cycle_start(db, household_id))
    else:
        first, last = month_bounds(a)
        cstart, cend = first, last + timedelta(days=1)
    is_current = cstart <= today < cend
    asof = today if is_current else cend - timedelta(days=1)   # live → partial pace; past → full period
    return asof, is_current, cstart.strftime("%Y-%m")


def _custom_default_targets(db: Session, household_id: int | None) -> dict[str, float]:
    """Per-category default targets, by name. Degrades to none if the column is not there yet, so a
    deploy that lands before its migration shows no targets rather than 500ing the Plan."""
    if db is None or household_id is None:
        return {}
    try:
        from models.category_settings import CategorySettings
        rows = (db.query(CategorySettings)
                .filter(CategorySettings.household_id == household_id,
                        CategorySettings.default_target_inr.isnot(None))
                .all())
        return {r.category: float(r.default_target_inr or 0.0) for r in rows}
    except Exception:  # noqa: BLE001 — a missing column must not take down the Plan
        return {}


def _cycle_projection(items: list[dict], net_spent: float, total_target: float,
                      days_elapsed: int, days_total: int, cstart) -> dict:
    """The three facts the cumulative chart makes, computed once, server-side.

      ahead_by   how far the running total is from where the budget's own straight line would put it
                 by now. Negative means behind it, which is the good direction.
      lands_at   where this pace ends the period, from the OWNED per-category projection — lumps
                 counted once, only the drip extrapolated. Never spent/elapsed*total.
      runs_out   the day the running total reaches the ceiling, if it does before the period ends.

    `runs_out_on` is null unless the crossing genuinely falls inside the period. A date computed past
    the end and then displayed anyway would read as a warning about this cycle while describing a day
    that is not in it.
    """
    from datetime import timedelta

    projected = round(sum(i.get("projected_inr") or 0.0 for i in items), 2)
    on_pace = round(total_target * days_elapsed / days_total, 2) if days_total else 0.0

    runs_out_on, days_before_end, runs_out_at = None, None, None
    if total_target > 0 and projected > total_target:
        # Where the projection line crosses the ceiling. The line runs from what is spent TODAY to
        # where the pace lands at the end, so the crossing is linear between those two points — the
        # same two points the chart draws.
        remaining_rise = projected - net_spent
        if remaining_rise > 0:
            frac = (total_target - net_spent) / remaining_rise
            day = days_elapsed + max(0.0, frac) * (days_total - days_elapsed)
            # `day` is a 0-BASED offset from the cycle start, so the cycle's last day is
            # `days_total - 1` and the crossing has to land on or before it.
            crossing_day = int(round(day))
            if 0 <= crossing_day <= days_total - 1:
                # TWO values for one crossing, because they are wanted at different precisions and
                # collapsing them puts the marker off the line. `runs_out_at` is the exact position
                # in the period, for geometry; `runs_out_on` is that day, for a human. Deriving the
                # marker's x from the rounded DATE moved it most of a day to the left and left the
                # dot floating ~Rs 6,000 below the ceiling it is supposed to be touching.
                runs_out_at = round(day / days_total, 6)
                runs_out_on = (cstart + timedelta(days=crossing_day)).isoformat()
                # Counted off the SAME rounded day the date uses, and off a 0-based offset. It was
                # `round(days_total - day)` against the unrounded value, which is two roundings of
                # one crossing and one off-by-one: the card read "runs out 29 Aug, 15 days before
                # the cycle ends" when 29 Aug to the last day (12 Sep) is 14.
                days_before_end = days_total - 1 - crossing_day

    return {
        "projected_inr": projected,
        "over_inr": round(max(0.0, projected - total_target), 2),
        "on_pace_inr": on_pace,
        "ahead_by_inr": round(net_spent - on_pace, 2),
        "runs_out_on": runs_out_on,
        "runs_out_at": runs_out_at,
        "days_before_end": days_before_end,
    }


def compute_budget(month: str | None = None, mode: str = "cycle", db: Session = None,
                   household_id: int | None = None, anchor: str | None = None):
    # `anchor` (ISO date) is the unambiguous addressing used by the UI's cycle stepper; `month` is the
    # legacy path (current-month callers: afford/debt/recovery/advisor/insights) and stays supported.
    if anchor:
        asof, is_current, month = _period_from_anchor(anchor, mode, db, household_id)
    else:
        month = _resolve_month(month, db)
        asof, is_current = anchor_for(month, mode, db, household_id)   # the lens fully decides the window (cycle vs calendar)
    spent = _spend_by_category(db, mode, asof, household_id)
    spent_ids = _spend_ids_by_category(db, mode, asof, household_id)

    q = db.query(Budget).filter(Budget.month == month)
    if household_id is not None:
        q = q.filter(Budget.household_id == household_id)
    budget_rows = q.all()

    # Targets are STICKY across months: if this month has none set yet (e.g. the 1st, right after a
    # rollover), carry forward the most recent month that does. Editing this month writes its own rows,
    # which then take precedence. Read-time only — never mutates data.
    carried_from = None
    if not budget_rows:
        pq = db.query(Budget).filter(Budget.month < month)
        if household_id is not None:
            pq = pq.filter(Budget.household_id == household_id)
        prior = pq.all()
        prior_months = sorted({b.month for b in prior}, reverse=True)
        if prior_months:
            carried_from = prior_months[0]
            budget_rows = [b for b in prior if b.month == carried_from]

    items = []
    if budget_rows:
        seen = set()
        for row in budget_rows:
            limit = round(row.monthly_limit_inr or 0.0, 2)
            spent_inr = spent.get(row.category, 0.0)
            items.append(
                {
                    "category": row.category,
                    "limit_inr": limit,
                    "spent_inr": spent_inr,
                    "remaining_inr": round(limit - spent_inr, 2),
                }
            )
            seen.add(row.category)
        # include spend categories that have no budget row yet
        for category, spent_inr in spent.items():
            if category in seen or category is None:
                continue
            items.append(
                {
                    "category": category,
                    "limit_inr": 0.0,
                    "spent_inr": spent_inr,
                    "remaining_inr": round(-spent_inr, 2),
                }
            )
    else:
        # no budget set — surface every spend category with a zero limit
        for category, spent_inr in spent.items():
            if category is None:
                continue
            items.append(
                {
                    "category": category,
                    "limit_inr": 0.0,
                    "spent_inr": spent_inr,
                    "remaining_inr": round(-spent_inr, 2),
                }
            )

    # A household's custom budget categories always appear on the Plan — even with no spend and no target
    # set yet — so a freshly-added category is immediately there to budget against (no dead-end). Built-in
    # categories keep their existing surface-on-spend-or-budget behaviour.
    present = {it["category"] for it in items}
    # A category's own default target seeds a month that has no budget row of its own, so a recurring
    # figure is set once rather than retyped every month. A month that HAS a row keeps it: this seeds,
    # it never overwrites something the household actually typed.
    defaults = _custom_default_targets(db, household_id)
    for name in custom_category_names(db, household_id):
        if name in present:
            continue
        spent_inr = spent.get(name, 0.0)
        limit_inr = round(defaults.get(name) or 0.0, 2)
        items.append({"category": name, "limit_inr": limit_inr, "spent_inr": spent_inr,
                      "remaining_inr": round(limit_inr - spent_inr, 2)})

    # Attach the exact txn ids behind each category so the drill reconciles to the rupee, any lens.
    for it in items:
        it["ids"] = spent_ids.get(it["category"], [])

    # Pace → projection. Window depends on the lens: per-card cycle (12th) or calendar month.
    from datetime import date, timedelta

    from services.cycle import card_cycle_spend, current_cycle_refunds, cycle_bounds, month_bounds
    today = asof   # compute pace/weeks/refunds AS-OF the requested period, not the wall clock
    if mode == "month":
        cstart, last = month_bounds(today)
        cend = last + timedelta(days=1)
    else:
        cstart, cend = cycle_bounds(today, settings_cycle_start(db, household_id))
    days_total = max(1, (cend - cstart).days)
    days_elapsed = max(1, (today - cstart).days + 1)
    days_left = max(0, days_total - days_elapsed)

    # Projected end-of-period spend. NOT spent/days*total — that scales one-off lumps (a bike service,
    # an annual premium) as if they recurred daily. Only genuinely recurring merchants get extrapolated.
    proj_by_cat: dict = {}
    try:
        from services import money_slice
        from services.projection import project_by_category
        allrows = money_slice.base_rows(db, household_id)
        lo, hi = cstart.isoformat(), cend.isoformat()
        cur_rows = [r for r in allrows if r.date and lo <= r.date[:10] < hi]
        hist_rows = [r for r in allrows if r.date and r.date[:10] < lo]
        hist_days = 0
        if hist_rows:
            ds = [r.date[:10] for r in hist_rows]
            hist_days = max(1, (date.fromisoformat(max(ds)) - date.fromisoformat(min(ds))).days + 1)
        proj_by_cat = project_by_category(cur_rows, hist_rows, days_elapsed=days_elapsed,
                                          days_total=days_total, history_days=hist_days)
    except Exception as exc:                 # fall back to the simple rate, but never silently:
        import logging  # a swallowed error here looks exactly like "no change"
        logging.getLogger(__name__).warning("projection failed, using flat rate: %r", exc)
        proj_by_cat = {}

    for it in items:
        p = proj_by_cat.get(it["category"])
        if p:
            it["projected_inr"] = p["projected_inr"]
            it["projected_low_inr"] = p["projected_low_inr"]     # nothing more happens
            it["projected_high_inr"] = p["projected_high_inr"]   # the old naive rate
            it["one_off_inr"] = p["one_off_inr"]                 # lumps, counted once not scaled
            it["recurring_inr"] = p["recurring_inr"]
            it["surprise_allowance_inr"] = p["surprise_allowance_inr"]
            it["projection_confidence"] = p["confidence"]
        else:
            # No rows this period (or the projector failed) — fall back to the flat rate, but still
            # publish low/high so every item carries the same shape and consumers need no None checks.
            flat = round(it["spent_inr"] / days_elapsed * days_total, 2)
            it["projected_inr"] = flat
            it["projected_low_inr"] = round(it["spent_inr"], 2)
            it["projected_high_inr"] = flat
            it["one_off_inr"] = 0.0
            it["recurring_inr"] = round(it["spent_inr"], 2)
            it["surprise_allowance_inr"] = 0.0
            it["projection_confidence"] = "low" if days_elapsed < 5 else "high"
        projected = it["projected_inr"]
        if it["limit_inr"] > 0 and it["spent_inr"] > it["limit_inr"]:
            it["status"] = "over"            # already breached — a fact, not a forecast
        elif (it["limit_inr"] > 0 and projected > it["limit_inr"]
              and it.get("projection_confidence") != "low"):
            it["status"] = "will_breach"     # on pace to breach (suppressed while the sample is tiny)
        else:
            it["status"] = "ok"

    from services.cycle import weekly_breakdown
    total_target = sum(it["limit_inr"] for it in items)
    weekly_target = round(total_target / max(1, days_total / 7), 2)
    # One call, one value. Reading it twice would let "spent" and "overshoot" disagree if the helper
    # ever became non-deterministic, and they are the same fact.
    net_spent = card_cycle_spend(db, today, mode=mode, household_id=household_id)

    return {
        "month": month,
        "mode": mode,
        # declare the Money Slice this tab reads, so it's explicit (not just consistent by accident)
        "slice": {"kind": "variable_spend",
                  "cycle_mode": "calendar_month" if mode == "month" else "instrument_cycle",
                  "sources": "all"},
        "is_current": is_current,       # is this the LIVE period (drives 'days left' vs 'closed')
        "carried_from": carried_from,   # non-null → these targets were inherited from that month
        "items": items,
        "refunds_inr": current_cycle_refunds(db, today, mode=mode, household_id=household_id),
        "net_spent_inr": net_spent,
        "cycle": {"days_total": days_total, "days_elapsed": days_elapsed, "days_left": days_left},
        # the true window this period covers — the UI labels the cycle by this (a June-12→July-11 cycle
        # reads "12 Jun – 11 Jul", not "July cycle"), which removes the calendar-month ambiguity.
        "period_start": cstart.isoformat(),
        "period_end": (cend - timedelta(days=1)).isoformat(),
        # total_target is what makes the weeks that have NOT started re-base on what is LEFT rather
        # than keep promising the slice they were given at the start of the cycle.
        "weeks": weekly_breakdown(db, today, weekly_target, mode=mode, household_id=household_id,
                                  total_target=total_target),
        "weekly_target": weekly_target,
        "total_target_inr": total_target,
        # What the cumulative chart is FOR: three facts about the running total, not about each week.
        # Published here because api/budget owns spend and its projection; a chart deriving any of
        # this in the browser would be a second definition of "on pace" that can drift from the Plan's
        # own category rows.
        "projection": _cycle_projection(items, net_spent, total_target,
                                        days_elapsed, days_total, cstart),
        # How far past the whole cycle budget this period already is. Published here rather than
        # subtracted in the frontend: api/budget owns spend, and a consumer re-deriving it would be a
        # second definition of "over budget" that can drift from this one. Zero while still inside it.
        "overshoot_inr": round(max(0.0, net_spent - total_target), 2),
    }


@router.get("/budget")
def get_budget(month: str | None = None, mode: str = "cycle", anchor: str | None = None,
               db: Session = Depends(get_db), household: Household = Depends(get_current_household)):
    return compute_budget(month=month, mode=mode, db=db, household_id=household.id, anchor=anchor)


@router.get("/budget/category-weeks")
def get_category_weeks(month: str | None = None, mode: str = "cycle", anchor: str | None = None,
                       db: Session = Depends(get_db),
                       household: Household = Depends(get_current_household)):
    """Category × week grid for the period (same week windows as the Plan weekly bar). Each cell carries
    its txn ids so the UI can drill straight to those rows. `anchor` (ISO date) = the cycle/month stepper."""
    from services.cycle import weekly_category_breakdown
    if anchor:
        asof, _, month = _period_from_anchor(anchor, mode, db, household.id)
    else:
        month = _resolve_month(month, db)
        asof, _ = anchor_for(month, mode, db, household.id)
    data = weekly_category_breakdown(db, asof, variable_category_values(db, household.id),
                                     mode=mode, household_id=household.id)
    return {"month": month, "mode": mode, **data}


def settings_cycle_start(db=None, household_id: int | None = None) -> int:
    """This household's cycle anchor (falls back to the global setting). Kept under the old name
    because other modules import it; the resolver itself lives in services.cycle."""
    from services.cycle import household_start_day
    return household_start_day(db, household_id)


@router.post("/budget")
def save_budget(payload: BudgetSaveIn = Body(...), db: Session = Depends(get_db),
                household: Household = Depends(get_current_household)):
    saved = 0
    for item in payload.items:
        row = (
            db.query(Budget)
            .filter(Budget.month == payload.month, Budget.category == item.category,
                    Budget.household_id == household.id)
            .first()
        )
        if row:
            row.monthly_limit_inr = item.monthly_limit_inr
        else:
            row = Budget(
                household_id=household.id,
                category=item.category,
                monthly_limit_inr=item.monthly_limit_inr,
                month=payload.month,
            )
            db.add(row)
        saved += 1

    db.commit()
    return {"saved": saved}
