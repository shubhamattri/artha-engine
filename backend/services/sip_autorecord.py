"""Record each SIP instalment as it happens, priced at the NAV it actually bought at.

A CAS statement gave these funds their real history. Without something to continue it, that history
starts rotting the day after the import: the ledger is declared complete, so nothing is invented on
top of it, which is correct — and it means an instalment that lands next month is simply absent until
someone imports another statement.

Everything needed to record it is already known. The app holds the standing instruction, and AMFI
publishes what the NAV was on any past date. So once a month, per fund, this writes the instalment
that has already happened: the amount that left the bank, the NAV of that day, and the units it
bought.

## Only for funds whose ledger is authoritative

For every other fund the monthly instalment is still SYNTHESISED at read time from
`monthly_contribution`. Writing a real entry there as well would count it twice — the same defect
this codebase has now hit in two different currencies. `ledger_is_authoritative` is the line: on one
side the ledger is the history and this fills it in, on the other the instruction is the history and
this must not touch it.

## The due day is derived, never assumed

Nothing stores which day of the month a SIP debits. The ledger knows: it is the day the household's
own recorded instalments actually landed on, most often. Deriving it from the record is the only
version that stays true when a mandate date changes, and a fund with no recorded history yet is
skipped rather than guessed at.

## What this cannot know

Whether an instalment actually went through. A bounced mandate, a paused SIP at the broker, or a
redemption are all invisible from here, and this will happily record an instalment that never left
the bank. That is why entries are tagged `auto` — a later CAS import can tell its own entries from
these and supersede them. This is a good estimate between statements, not a substitute for one.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date

from models.recurring import RecurringInvestment
from services import mf_nav
from sqlalchemy.orm import Session

log = logging.getLogger("artha.sip_auto")


def _due_day(entries: list[dict]) -> int | None:
    """The day of the month this SIP actually lands on, by its own record."""
    days = [int(c["date"][8:10]) for c in entries if c.get("date") and (c.get("amount") or 0) > 0]
    if not days:
        return None
    return Counter(days).most_common(1)[0][0]


def _month_starts(after: date, until: date) -> list[date]:
    """Every month strictly after `after`'s month, up to and including `until`'s."""
    out: list[date] = []
    y, m = after.year, after.month
    while True:
        m += 1
        if m > 12:
            y, m = y + 1, 1
        if (y, m) > (until.year, until.month):
            break
        out.append(date(y, m, 1))
    return out


def _due_dates(inv: RecurringInvestment, today: date) -> list[date]:
    entries = list(inv.contributions or [])
    day = _due_day(entries)
    if day is None:
        return []
    last = max((c["date"] for c in entries if c.get("date")), default=None)
    if last is None:
        return []
    last_d = date.fromisoformat(last[:10])
    out = []
    for first in _month_starts(last_d, today):
        # A short month cannot have a 31st; the mandate lands on its last day instead.
        d = min(day, [31, 29 if first.year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][first.month - 1])
        due = date(first.year, first.month, d)
        if due <= today:                      # never record an instalment that has not happened
            out.append(due)
    return out


def record_due(db: Session, household_id: int | None = None, today: date | None = None) -> dict:
    """Append the instalments that have come due since each fund's last recorded entry."""
    today = today or date.today()
    q = db.query(RecurringInvestment).filter(
        RecurringInvestment.kind == "sip",
        RecurringInvestment.active.is_(True),
        RecurringInvestment.ledger_is_authoritative.is_(True),
    )
    if household_id is not None:
        q = q.filter(RecurringInvestment.household_id == household_id)

    added = 0
    unpriced = 0
    touched = 0
    for inv in q.all():
        if not inv.monthly_contribution:
            continue
        code = (inv.market or {}).get("scheme_code")
        if not code:
            continue
        new: list[dict] = []
        for due in _due_dates(inv, today):
            hit = mf_nav.fetch_nav_on(code, due)
            if not hit or not hit[0]:
                # No NAV for that day yet. Leaving the gap is right: it will be filled on a later run
                # once the feed catches up, and a made-up price would be indistinguishable from a real
                # one the moment it is written.
                unpriced += 1
                continue
            nav = float(hit[0])
            amt = float(inv.monthly_contribution)
            new.append({
                "date": due.isoformat(), "amount": round(amt, 2),
                "units": round(amt / nav, 4), "nav": round(nav, 4), "nav_date": hit[1],
                "funded_from": "bank", "direction": "buy", "auto": True,
                "note": "SIP instalment, recorded automatically",
            })
        if new:
            ledger = list(inv.contributions or []) + new
            ledger.sort(key=lambda c: c.get("date") or "")
            inv.contributions = ledger
            db.add(inv)
            added += len(new)
            touched += 1
    if added:
        db.commit()
    return {"funds": touched, "instalments": added, "unpriced": unpriced}
