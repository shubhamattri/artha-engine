"""Give each fund its real transaction history, from a CAS statement.

Every fund in this household was anchored to the broker on one day, which is why none of them can
report an XIRR: the app only annualises past ninety days from a holding's anchor, and the anchor is
younger than that. The money is older than the anchor. A CAS is where the real dates live.

## Why this is a planner, not a writer

It reports what it would do and changes nothing unless asked. A history import rewrites the basis
every return on the screen is measured against, so it has to be readable before it is real.

## Three things that are easy to get wrong here

**Folios are not holdings.** An ELSS fund accumulates a separate folio per lock-in tranche, so one
scheme legitimately appears several times in the same statement. This household holds one ELSS across
three folios whose units only add up to the app's single row when summed. The join key is therefore
the ISIN, and every folio carrying it belongs to one holding.

**ISIN is the only reliable join.** The app stores AMFI scheme codes; a CAS carries ISINs and RTA
scheme codes. AMFI's own feed publishes `isin_growth` per scheme code, so the mapping is looked up
rather than guessed. Matching on fund NAME would appear to work and would quietly mis-assign the day
an AMC renames a plan, which they do.

**The ledger has to declare itself complete.** Writing real instalments while the app still generates
synthetic ones from the standing instruction counts every purchase twice. `ledger_is_authoritative`
is what stops that, and this importer sets it in the same transaction as the entries. Importing
without it would inflate the invested base and the XIRR, silently.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from models.recurring import RecurringInvestment
from parsers.cas_pdf import CasScheme, CasTransaction
from services import mf_nav
from sqlalchemy.orm import Session

log = logging.getLogger("artha.cas_import")


def fingerprint(t: CasTransaction) -> str:
    """Stable identity for one CAS movement, so a re-import adds nothing.

    Date, amount and units together, because a fund can legitimately transact twice on one day (an
    ELSS instalment landing in two folios), and dropping units from the key would collapse those into
    one. Insertion order is deliberately not part of it — order is not identity.
    """
    raw = f"{t.date}|{t.amount:.2f}|{t.units:.4f}|{t.nav:.4f}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


@dataclass
class FundPlan:
    inv_id: int
    name: str
    scheme_code: str
    isin: str
    folios: int
    new_entries: list[dict] = field(default_factory=list)
    already_present: int = 0
    new_anchor: str | None = None
    invested_now: float = 0.0
    invested_after: float = 0.0


@dataclass
class ImportPlan:
    funds: list[FundPlan] = field(default_factory=list)
    unmatched_isins: list[tuple[str, str, float]] = field(default_factory=list)
    unresolved_funds: list[tuple[str, str]] = field(default_factory=list)

    def describe(self) -> str:
        out: list[str] = []
        for f in self.funds:
            out.append(
                f"  {f.name[:34]:36} +{len(f.new_entries):>3} new, {f.already_present:>3} already there"
                f"  anchor→{f.new_anchor}  invested {round(f.invested_now):,} → {round(f.invested_after):,}"
            )
        if self.unresolved_funds:
            out.append("\n  no ISIN resolved for these app rows (left untouched):")
            out += [f"    {n} (scheme {c})" for c, n in self.unresolved_funds]
        if self.unmatched_isins:
            out.append("\n  in the statement but not in the app (NOT created):")
            out += [f"    {i}  {n[:44]}  units {u:.3f}" for i, n, u in self.unmatched_isins]
        return "\n".join(out) or "  nothing to do"


def _isin_of(scheme_code: str | None) -> str | None:
    """The ISIN AMFI publishes for a scheme code. None when the feed cannot say."""
    if not scheme_code:
        return None
    meta = mf_nav.fetch_meta(scheme_code)
    return (meta or {}).get("isin_growth") or None


def plan_import(db: Session, schemes: list[CasScheme], household_id: int) -> ImportPlan:
    """Work out what importing this statement would change. Reads only."""
    by_isin: dict[str, list[CasScheme]] = {}
    for s in schemes:
        if s.isin:
            by_isin.setdefault(s.isin, []).append(s)

    plan = ImportPlan()
    rows = (
        db.query(RecurringInvestment)
        .filter(RecurringInvestment.household_id == household_id,
                RecurringInvestment.kind == "sip")
        .all()
    )
    claimed: set[str] = set()
    for inv in rows:
        code = (inv.market or {}).get("scheme_code")
        isin = _isin_of(code)
        if not isin:
            plan.unresolved_funds.append((str(code), inv.name))
            continue
        group = by_isin.get(isin)
        if not group:
            plan.unresolved_funds.append((str(code), inv.name))
            continue
        claimed.add(isin)

        txns = sorted((t for s in group for t in s.transactions), key=lambda t: t.date)
        have = {c.get("cas_fp") for c in (inv.contributions or []) if c.get("cas_fp")}
        fp = FundPlan(inv_id=inv.id, name=inv.name, scheme_code=str(code), isin=isin,
                      folios=len(group))
        for t in txns:
            f = fingerprint(t)
            if f in have:
                fp.already_present += 1
                continue
            fp.new_entries.append({
                "date": t.date, "amount": round(t.amount, 2), "units": round(t.units, 4),
                "nav": round(t.nav, 4), "cas_fp": f, "funded_from": "bank",
                "direction": "redeem" if t.amount < 0 else "buy",
                "note": "CAS import",
            })
        if txns:
            fp.new_anchor = txns[0].date
        fp.invested_now = float(inv.opening_balance) + sum(
            float(c.get("amount", 0) or 0) for c in (inv.contributions or []))
        fp.invested_after = round(sum(t.amount for t in txns), 2)
        plan.funds.append(fp)

    for isin, group in by_isin.items():
        if isin in claimed:
            continue
        units = sum(s.closing_units for s in group)
        if units > 0:                       # an exited scheme is history, not a missing holding
            plan.unmatched_isins.append((isin, group[0].scheme_name, units))
    return plan


def apply_import(db: Session, plan: ImportPlan) -> dict:
    """Write the planned entries. Only ever called after a human has read the plan."""
    touched = 0
    added = 0
    for f in plan.funds:
        if not f.new_entries:
            continue
        inv = db.get(RecurringInvestment, f.inv_id)
        if inv is None:
            continue
        ledger = list(inv.contributions or []) + f.new_entries
        ledger.sort(key=lambda c: c.get("date") or "")
        inv.contributions = ledger
        # The statement IS the history now: anchor to its first movement, drop the opening balance it
        # replaces, and stop the standing instruction being accrued on top of real entries.
        if f.new_anchor:
            from datetime import date as _date
            inv.as_of_date = _date.fromisoformat(f.new_anchor)
        inv.opening_balance = 0.0
        inv.ledger_is_authoritative = True
        db.add(inv)
        touched += 1
        added += len(f.new_entries)
    db.commit()
    return {"funds_updated": touched, "entries_added": added}
