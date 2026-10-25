"""Billing/budget cycle — the household's real financial month.

The first household's cycle runs 12th→12th (cards being aligned to it). Everything that says "this
month" — cockpit, spend, afford, budget — should calibrate to THIS window, not the calendar month.
Anchor day is configurable per household (settings.cycle_start_day, default 12). Pure date math.
"""

from __future__ import annotations

from datetime import date

_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _add_month(d: date) -> date:
    """First-of-next-month + same day, clamped to month length."""
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    # clamp day (e.g. start_day 31 → Feb 28)
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 28)


def _sub_month(d: date) -> date:
    y, m = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 28)


def _clamp_to_month(y: int, m: int, day: int) -> date:
    """`day` inside month (y, m), pulled back to that month's last day when it overruns.
    A card billing on the 31st bills on 28 Feb (29 in a leap year) — the cycle must follow, not
    silently snap every household to the 28th of every month."""
    from calendar import monthrange
    return date(y, m, min(max(1, day), monthrange(y, m)[1]))


def cycle_bounds(today: date, start_day: int = 12) -> tuple[date, date]:
    """The [start, end) cycle window containing `today`. End is exclusive (next cycle's start).

    `start_day` may be 1–31. Months too short for it clamp to their last day, so a 29th/30th/31st
    billing day is honoured everywhere it exists instead of being capped at 28."""
    start_day = max(1, min(31, start_day))
    anchor = _clamp_to_month(today.year, today.month, start_day)
    if today >= anchor:
        start = anchor
    else:
        py, pm = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        start = _clamp_to_month(py, pm, start_day)
    ny, nm = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return start, _clamp_to_month(ny, nm, start_day)


def cycle_label(today: date, start_day: int = 12) -> str:
    """Human label, e.g. '12 Jun – 11 Jul'."""
    start, end = cycle_bounds(today, start_day)
    last = _sub_day(end)
    return f"{start.day} {_MONTHS[start.month]} – {last.day} {_MONTHS[last.month]}"


def _sub_day(d: date) -> date:
    return date.fromordinal(d.toordinal() - 1)


_NON_CARD_TYPES = ("account", "manual", "cash", "sms")


def _q_cycle_day(db, household_id):
    from models.tenant import Household
    return db.query(Household.cycle_start_day).filter(Household.id == household_id).first()


def _q_card_cfg(db, household_id):
    from models.tenant import Household
    return (db.query(Household.per_card_cycles, Household.card_cycle_days,
                     Household.card_cycle_default)
            .filter(Household.id == household_id).first())


def _session_cache(db) -> dict:
    """Per-request memo, hung off the SQLAlchemy Session.

    card_start_day() is called once per transaction and re-reads the household's billing config every
    time, so a 6,000-row ledger issued ~38,000 SQL statements to answer the same two questions over
    and over — 7.2s of a 7.5s page load. A household's cycle configuration cannot change midway
    through a request, so it is read once and reused. Session scope (not module scope) is the point:
    a new request gets a fresh session and therefore fresh config, and one household can never read
    another's cached value.
    """
    if db is None:
        return {}
    try:
        return db.info.setdefault("_artha_cycle_cache", {})
    except Exception:  # noqa: BLE001 — not a real Session (tests may pass a stub)
        return {}


def _safe_first(db, fn):
    """Run a settings lookup inside a SAVEPOINT. If the column doesn't exist yet (deploy lands code
    before the migration), the failure rolls back only the savepoint — without this, the aborted
    statement poisons the whole session and EVERY later query dies with InFailedSqlTransaction."""
    try:
        with db.begin_nested():
            return fn()
    except Exception:
        return None


def card_cycle_config(db=None, household_id: int | None = None) -> tuple[bool, dict, int]:
    """(per_card_cycles, card_cycle_days, default) for THIS household, falling back to the global
    settings when unset. Per-household because household A's HDFC card may bill on the 22nd while
    household B's bills on the 5th — a global map would silently mis-window B's whole ledger."""
    from config import settings
    on = bool(getattr(settings, "per_card_cycles", False))
    days = dict(getattr(settings, "card_cycle_days", {}) or {})
    default = int(getattr(settings, "card_cycle_default", 22) or 22)
    if db is not None and household_id is not None:
        cache = _session_cache(db)
        key = ("card_cfg", household_id)
        if key in cache:
            row = cache[key]
        else:
            row = _safe_first(db, lambda: _q_card_cfg(db, household_id))
            cache[key] = row
        if row:
            if row[0] is not None:
                on = bool(row[0])
            if row[1]:
                days = dict(row[1])
            if row[2]:
                default = int(row[2])
    return on, days, default


def card_start_day(bank: str | None, card_type: str | None, db=None,
                   household_id: int | None = None) -> int:
    """The billing-cycle anchor day for a transaction's instrument. Credit cards bill on their own
    day when per_card_cycles is on; cash/UPI/bank accounts always follow the household cycle."""
    on, days, default = card_cycle_config(db, household_id)
    hh = household_start_day(db, household_id)
    if not on:
        return hh                                   # unified household window for EVERY instrument
    ct = (card_type or "").strip().lower()
    if not ct or any(t in ct for t in _NON_CARD_TYPES):  # not a credit card → household cycle
        return hh
    b = (bank or "").lower()
    for key, day in days.items():
        if key in b:
            return day
    return default                                  # any other credit card


def cycle_start_date(today: date, start_day: int) -> date:
    """The most recent cycle-start on/before today, for the given anchor day."""
    return cycle_bounds(today, start_day)[0]


def in_current_cycle(txn_date: str, bank: str | None, card_type: str | None, today: date,
                     db=None, household_id: int | None = None) -> bool:
    """Is this transaction in its instrument's CURRENT billing cycle (per-card windows)?"""
    if not txn_date or txn_date[:10] > today.isoformat():
        return False
    start = cycle_start_date(today, card_start_day(bank, card_type, db, household_id)).isoformat()
    return txn_date[:10] >= start


def month_bounds(today: date) -> tuple[date, date]:
    """Calendar-month window [first, last] containing today."""
    first = today.replace(day=1)
    nxt = (first.replace(day=28) + _td(7)).replace(day=1)
    return first, nxt - _td(1)


def _td(days):
    from datetime import timedelta
    return timedelta(days=days)


def _spend_query(db, *, direction: str = "debit", txn_types=("SPEND", "FEE"), categories=None,
                 household_id: int | None = None):
    """The ACTIVE-rows predicate, in one place. Windowing it by period gives the current cycle;
    leaving it unwindowed gives the whole history. Two callers, one definition of 'counts'."""
    from models.transaction import Transaction
    q = (db.query(Transaction)
         .filter(Transaction.direction == direction, Transaction.dup_of_id.is_(None),
                 Transaction.archived.is_(False)))
    if household_id is not None:
        q = q.filter(Transaction.household_id == household_id)
    if txn_types:
        q = q.filter(Transaction.txn_type.in_(list(txn_types)))
    if categories:
        q = q.filter(Transaction.category.in_(list(categories)))
    return q


def all_spend_rows(db, *, direction: str = "debit", txn_types=("SPEND", "FEE"), categories=None,
                   household_id: int | None = None):
    """Every active row of this kind, across all time. What 'ordinary' costs is a property of the
    household's whole history, not of whichever period is on screen."""
    return _spend_query(db, direction=direction, txn_types=txn_types, categories=categories,
                        household_id=household_id).all()


def current_cycle_rows(db, today: date, *, mode: str = "cycle", direction: str = "debit",
                       txn_types=("SPEND", "FEE"), categories=None, household_id: int | None = None):
    """ACTIVE (non-duplicate) transactions for the chosen LENS. ONE source of truth for Home, Plan,
    Spending — every screen reconciles because they all read this.
      mode='cycle' → each card's billing cycle (ICICI 12th, HDFC/Axis 22nd) — the real view.
      mode='month' → the plain calendar month (1st–end, all cards) — the simple view.
    Counts every source; dedup/archive excluded. Scoped to one household when household_id is given;
    None = all (single-household safe)."""
    q = _spend_query(db, direction=direction, txn_types=txn_types, categories=categories,
                     household_id=household_id)
    if mode == "month":
        first, last = month_bounds(today)
        fi, la = first.isoformat(), last.isoformat()
        return [t for t in q.all() if t.date and fi <= t.date[:10] <= la]
    return [t for t in q.all()
            if in_current_cycle(t.date, t.bank, t.card_type, today, db, household_id)]


def plan_categories(db=None, household_id: int | None = None) -> list[str]:
    """The everyday spend buckets — the scope of 'spend' shown on Home AND the Plan: the 10 built-ins
    plus this household's custom budget categories (when a session/household is given)."""
    from services.categories import variable_category_values
    return variable_category_values(db, household_id)


def current_cycle_refunds(db, today: date, *, mode: str = "cycle", household_id: int | None = None) -> float:
    """Total money-back (refund credits) this period — netted at the period level, not forced into a
    category (a refund doesn't reliably tell you which purchase/category it was for)."""
    return round(sum(r.amount or 0.0 for r in
                     current_cycle_rows(db, today, mode=mode, direction="credit", txn_types=("REFUND",),
                                        household_id=household_id)), 2)


def card_cycle_spend(db, today: date, *, mode: str = "cycle", sources: list[str] | None = None,
                     txn_types=("SPEND", "FEE"), household_id: int | None = None) -> float:
    """NET spend this period = gross category spend − total refunds. Refunds net at the period level;
    per-category stays GROSS so each category reconciles with its own transaction list."""
    gross = sum(current_cycle_spend_by_category(db, today, plan_categories(db, household_id), mode=mode,
                                                household_id=household_id).values())
    return round(gross - current_cycle_refunds(db, today, mode=mode, household_id=household_id), 2)


def weekly_breakdown(db, today: date, weekly_target: float = 0.0, *, mode: str = "cycle",
                     household_id: int | None = None, total_target: float = 0.0) -> list[dict]:
    """Split the current period into 7-day weeks and total the spend in each — so you can see 'am I
    on track THIS week?'

    A week that has STARTED is judged against what was left ON THE DAY IT OPENED, spread over the
    days from there to the end of the cycle. Weeks that have NOT started share what survives the
    current week's reservation. Both are the same rule; only the second has to project, because a
    week that has not begun cannot know what will be spent before it does.

    Pass `total_target` to get that re-basing; without it every bucket keeps the flat pace, which is
    what "what was the plan" wants, as opposed to "what can I still spend".

    A flat pace alone was misleading in exactly one direction, and it was the expensive one: overspend
    week 1 and weeks 2-5 each went on promising the original slice, so the chart showed money as
    available that had already left the account. Re-basing is not a preference, it is the difference
    between a plan and a reminder of a plan.

    The line for a week in progress deliberately does NOT move. If it did, a bar that crept over its
    target would be indistinguishable from one whose target slid underneath it, and neither the
    household nor this function could tell you which had happened.
    """
    from datetime import timedelta
    cstart, cend = (month_bounds(today)[0], month_bounds(today)[1] + timedelta(days=1)) if mode == "month" \
        else cycle_bounds(today, household_start_day(db, household_id))
    # SAME scope as the Plan total: only the budget (variable) categories — NOT rent/insurance/EMIs,
    # which are fixed and tracked separately. Otherwise the weeks sum to more than "Spent".
    rows = current_cycle_rows(db, today, mode=mode, categories=plan_categories(db, household_id),
                              household_id=household_id)

    # Pass 1 — cut the buckets and total each one. Targets wait for pass 2 because a future week's
    # share depends on how many future days there are in total, which is not known until the last
    # bucket has been cut.
    buckets: list[dict] = []
    wk_start, i = cstart, 1
    while wk_start < cend:
        # TRUE 7-day weeks from the cycle start, and the leftover days stand as their own final bucket.
        # A 31-day cycle is 4 weeks + 3 days = 5 buckets, not 3 weeks + a 10-day "week". The UI shows each
        # bucket's dates and day count, so a short tail reads correctly instead of needing to be hidden.
        wk_end = min(wk_start + timedelta(days=7), cend)
        buckets.append({
            "week": i, "start": wk_start, "end": wk_end, "span": (wk_end - wk_start).days,
            "spent": round(sum(r.amount or 0.0 for r in rows
                               if wk_start.isoformat() <= r.date[:10] < wk_end.isoformat()), 2),
            "is_current": wk_start <= today < wk_end,
            "is_past": wk_end <= today,
            "is_future": wk_start > today,
        })
        wk_start, i = wk_end, i + 1

    # Pass 2 — what is genuinely left, and how many days are waiting to receive it.
    #
    # Spend is counted only up to TODAY. A transaction dated in a future week is not money gone; it is
    # money scheduled, and letting it shrink that week's allowance in advance would charge the
    # household twice for one debit — once now as a smaller target, once later as the actual spend.
    today_iso = today.isoformat()
    spent_to_date = round(sum(r.amount or 0.0 for r in rows if r.date[:10] <= today_iso), 2)
    # Floored at zero. Once the cycle budget is gone the honest per-week answer is "nothing", and a
    # negative target would be both unactionable and repeated in every remaining column. The SIZE of
    # the overshoot is published separately (api/budget → overshoot_inr) and said once.
    remaining = max(0.0, total_target - spent_to_date)
    future_days = sum(b["span"] for b in buckets if b["is_future"])

    # The week IN PROGRESS is neither past nor future, and that gap is where the money leaked.
    #
    # `remaining` has to cover every day that has not happened yet, which includes the rest of the
    # week you are standing in. But only the weeks that have NOT STARTED divide it up, and the
    # current week keeps a fixed line of its own. So its unspent allowance was offered twice: once
    # as its own headroom, once inside the pool the later weeks share. So the chart offered money
    # the household did not have, and because the pool was divided over the future weeks' days only,
    # an overspend early in the cycle came back as a RAISE for the later weeks.
    #
    # That distinction is the whole bug. A week that opened after an overspend still carried the flat
    # line it was given at the start of the cycle, while the weeks after it WERE re-based, so the bar
    # showed one household two different daily rates at once, same cycle, same pot.
    # An overspend has to slow every week that follows it, including the one you are standing in.
    #
    # Note this collapses to the flat pace for the opening bucket, where nothing has been spent yet,
    # so it is one rule rather than two.
    def started_target(b: dict) -> float:
        """What a bucket that has OPENED may spend, fixed at the moment it opened."""
        if not total_target:
            # "What WAS the plan" mode — every bucket keeps the flat pace. Two test callers rely on
            # this; no production caller takes it.
            return round(weekly_target * b["span"] / 7, 2)
        start_iso = b["start"].isoformat()
        before = round(sum(r.amount or 0.0 for r in rows
                           if r.date[:10] < start_iso and r.date[:10] <= today_iso), 2)
        # `days_from_start` is at least 1: the loop above only cut this bucket because
        # `b["start"] < cend`.
        return round(max(0.0, total_target - before) / (cend - b["start"]).days * b["span"], 2)

    # Reserve what the current week may still spend before the rest divide the pool, or its headroom
    # is promised twice — once as its own line, once inside theirs.
    #
    # `min(remaining, ...)` cannot bind today: a bucket's line is a FRACTION (its length over the
    # days that remained when it opened) of the money that remained then, so the reservation is
    # always within `remaining`. Kept anyway, because it is free and the proof depends entirely on
    # how `started_target` is defined — change that and this is the only thing between a household
    # and a negative budget in every column.
    current = next((b for b in buckets if b["is_current"]), None)
    reserved = 0.0
    if total_target and current is not None:
        reserved = min(remaining, max(0.0, started_target(current) - current["spent"]))
    remaining_for_future = max(0.0, remaining - reserved)

    # The LAST future bucket takes whatever the rounding left behind, so the future targets tile
    # `remaining` to the paisa. Three weeks sharing 22,000 round to 7,333.33 each and lose a paisa;
    # small, but this is the number that is supposed to answer "does what is left add up", and a
    # budget that does not sum is the exact complaint this function exists to fix.
    last_future = max((b["week"] for b in buckets if b["is_future"]), default=None)
    allocated = 0.0

    weeks: list[dict] = []
    for b in buckets:
        if total_target and b["is_future"]:
            # future_days is zero only in the final bucket, where there is no future left to divide
            # into and nothing to divide — guarded because the alternative is a 500 on the Plan page.
            if not future_days:
                target = 0.0
            elif b["week"] == last_future:
                target = round(remaining_for_future - allocated, 2)
            else:
                target = round(remaining_for_future * b["span"] / future_days, 2)
                allocated += target
        else:
            target = started_target(b)
        weeks.append({
            # `end` is INCLUSIVE (the bucket's last day), so a reader cannot recover the day count
            # by subtracting the two dates without knowing that. `span` is published rather than
            # left to be re-derived: the same off-by-one convention already produced "runs out
            # 29 Aug, 15 days before the cycle ends" one module over.
            "week": b["week"], "start": b["start"].isoformat(),
            "end": (b["end"] - timedelta(days=1)).isoformat(), "span": b["span"],
            "spent": b["spent"], "target": target,
            "is_current": b["is_current"], "is_future": b["is_future"],
            "on_track": (b["spent"] <= target) if (target and (b["is_past"] or b["is_current"])) else None,
        })
    return weeks


def weekly_category_breakdown(db, today: date, categories, *, mode: str = "cycle",
                              household_id: int | None = None) -> dict:
    """Per-category spend split into the SAME 7-day weeks as `weekly_breakdown`, plus the txn ids behind
    each (category × week) cell so the UI can drill to exactly those rows. Read-only. Mirrors the week
    windowing (incl. short trailing-stub absorption) so the grid lines up with the weekly bar."""
    from datetime import timedelta
    cstart, cend = (month_bounds(today)[0], month_bounds(today)[1] + timedelta(days=1)) if mode == "month" \
        else cycle_bounds(today, household_start_day(db, household_id))
    rows = current_cycle_rows(db, today, mode=mode, categories=categories, household_id=household_id)
    windows: list[tuple[int, date, date]] = []
    wk_start, i = cstart, 1
    while wk_start < cend:
        wk_end = min(wk_start + timedelta(days=7), cend)   # true 7-day weeks; leftover is its own bucket
        windows.append((i, wk_start, wk_end))
        wk_start, i = wk_end, i + 1
    weeks_meta = [{"week": wi, "start": s.isoformat(), "end": (e - timedelta(days=1)).isoformat()}
                  for wi, s, e in windows]
    per_cat: dict[str, dict] = {c: {"category": c, "weeks": [0.0] * len(windows),
                                    "txn_ids": [[] for _ in windows], "total": 0.0} for c in categories}
    for r in rows:
        cell = per_cat.get(r.category)
        if cell is None or not r.date:
            continue
        d = r.date[:10]
        for idx, (_, s, e) in enumerate(windows):
            if s.isoformat() <= d < e.isoformat():
                amt = r.amount or 0.0
                cell["weeks"][idx] = round(cell["weeks"][idx] + amt, 2)
                cell["txn_ids"][idx].append(r.id)
                cell["total"] = round(cell["total"] + amt, 2)
                break
    cats = sorted((v for v in per_cat.values() if v["total"] > 0), key=lambda v: -v["total"])
    return {"weeks": weeks_meta, "categories": cats}


def household_start_day(db=None, household_id: int | None = None) -> int:
    """This household's cycle anchor day. Reads the household row; falls back to the global setting
    when there's no db/household or the column is null. THE single source — every caller goes through
    here so household B never inherits household A's billing day."""
    from config import settings
    fallback = int(getattr(settings, "cycle_start_day", 12) or 12)
    if db is None or household_id is None:
        return fallback
    cache = _session_cache(db)
    key = ("cycle_day", household_id)
    if key in cache:
        row = cache[key]
    else:
        row = _safe_first(db, lambda: _q_cycle_day(db, household_id))
        cache[key] = row
    if row and row[0]:
        return max(1, min(31, int(row[0])))
    return fallback


def _household_start(db=None, household_id: int | None = None) -> int:
    """Back-compat alias for household_start_day."""
    return household_start_day(db, household_id)


def current_cycle_spend_by_category(db, today: date, categories, *, mode: str = "cycle",
                                    household_id: int | None = None) -> dict[str, float]:
    """Per-category GROSS spend (debits only) for the chosen lens — matches each category's
    transaction list exactly. Refunds are shown separately (current_cycle_refunds)."""
    out: dict[str, float] = {}
    for r in current_cycle_rows(db, today, mode=mode, categories=categories, household_id=household_id):
        out[r.category] = round(out.get(r.category, 0.0) + (r.amount or 0.0), 2)
    return out


def cycle_info(today: date, start_day: int = 12) -> dict:
    start, end = cycle_bounds(today, start_day)
    last = _sub_day(end)
    return {
        "start_day": start_day,
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "last": last.isoformat(),
        "label": cycle_label(today, start_day),
        "days_left": (last - today).days,
    }


def invalidate(db, household_id: int | None = None) -> None:
    """Drop the memoised cycle config for this session.

    The memo assumes a household's configuration cannot change midway through a request, which holds
    for every caller except the one that CHANGES it. Saving a new cycle day then reading it back in
    the same request returned the old value, so the UI reported the save as a no-op and the user's
    change looked rejected. Anything writing household cycle config must call this.
    """
    cache = _session_cache(db)
    if cache is None:
        return
    for key in [k for k in cache if isinstance(k, tuple) and len(k) == 2
                and (household_id is None or k[1] == household_id)]:
        cache.pop(key, None)
