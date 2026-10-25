"""Recurring/accruing investments — SIP, RD, EPF, PPF.

Value is COMPUTED (services.accrue), never fetched. Endpoints:
  GET    /recurring               list + computed values + totals
  POST   /recurring               add an instrument
  PUT    /recurring/{id}          edit (rate change, contribution change, ...)
  POST   /recurring/{id}/contribute   log an ad-hoc lump sum ("we added X")
  DELETE /recurring/{id}
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from api.deps import get_current_household
from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from models.recurring import RecurringInvestment
from models.tenant import Household
from pydantic import BaseModel
from services import accrue, mf_nav
from sqlalchemy.orm import Session

router = APIRouter()

NAV_STALE = timedelta(hours=6)   # re-fetch a fund's NAV at most every 6h (like stocks)

# Sensible defaults per instrument (rate + compounding) — editable per row.
KIND_DEFAULTS = {
    "sip":  {"compounding": "monthly",   "annual_rate_pct": 12.0},  # assumed market return
    "rd":   {"compounding": "quarterly", "annual_rate_pct": 7.0},
    "fd":   {"compounding": "quarterly", "annual_rate_pct": 6.5},
    "epf":  {"compounding": "annual",    "annual_rate_pct": 8.25},
    "ppf":  {"compounding": "annual",    "annual_rate_pct": 7.1},
}

# Net-worth category each instrument rolls up into.
KIND_CATEGORY = {"sip": "Market", "epf": "Retirement", "ppf": "Retirement",
                 "rd": "Deposits", "fd": "Deposits"}


class RecurringIn(BaseModel):
    name: str
    kind: str                              # sip | rd | epf | ppf
    # Literal, not str: an unknown purpose would silently land in the corpus, so reject it at the edge.
    purpose: Literal["wealth", "donation"] = "wealth"
    holder: str | None = None
    opening_balance: float = 0.0
    as_of_date: date
    monthly_contribution: float = 0.0
    employer_contribution: float = 0.0
    annual_rate_pct: float | None = None   # falls back to KIND_DEFAULTS
    compounding: str | None = None
    maturity_date: date | None = None
    active: bool = True
    from_takehome: bool = True
    scheme_code: str | None = None     # AMFI code → market-priced (MF)
    units: float | None = None         # units held → value = units × live NAV
    current_value: float | None = None  # MF: give value instead of units → units backed out from live NAV
    notes: str | None = None


class ContributeIn(BaseModel):
    # Exactly one of amount/units. Units exist because brokers report redemptions in units, and
    # converting them by hand at a NAV you looked up today gets the rupee figure wrong.
    amount: float | None = None
    units: float | None = None
    date: date
    note: str | None = None
    # wallet | bank | other. Defaults to bank: a contribution made without choosing must never
    # quietly drain the wallet, the same rule loan payments follow.
    funded_from: str | None = None


class SkipIn(BaseModel):
    amount: float               # how much of this month's contribution to skip (0 = un-skip)
    month: str | None = None    # "YYYY-MM"; defaults to the current month


def _is_market(inv: RecurringInvestment) -> bool:
    m = inv.market or {}
    return bool(m.get("scheme_code") and m.get("units"))


def _refresh_nav(inv: RecurringInvestment, db: Session | None, force: bool = False) -> tuple[float | None, str | None]:
    """Return (nav, as_of). Re-fetches from the feed if forced or the cached NAV is >6h old."""
    m = dict(inv.market or {})
    now = datetime.now(timezone.utc)
    last_at = m.get("last_nav_at")
    stale = (
        force or not m.get("last_nav") or not last_at
        or datetime.fromisoformat(last_at) < now - NAV_STALE
    )
    if stale and db is not None:
        fetched = mf_nav.fetch_nav(m.get("scheme_code"))
        if fetched:
            m["last_nav"] = fetched[0]
            m["nav_date"] = fetched[1]
            m["last_nav_at"] = now.isoformat()
            inv.market = m
            db.add(inv)
    return m.get("last_nav"), m.get("nav_date")


def _view(inv: RecurringInvestment, today: date, db: Session | None = None, force_nav: bool = False) -> dict:
    market_extra = {}
    # partial skips: a month's contribution reduced by an amount (free cash without pausing the whole SIP).
    # `skip_total` = money that never got invested (reduces value + contributed); `skip_now` = this month's
    # reduction (reduces the take-home debit this cycle).
    skips = inv.skips or []
    skip_total = round(sum(float(s.get("amount", 0) or 0) for s in skips), 2)
    this_month = today.strftime("%Y-%m")
    skip_now = round(sum(float(s.get("amount", 0) or 0) for s in skips if s.get("month") == this_month), 2)
    if _is_market(inv):
        m = inv.market or {}
        # `market.units` is the ANCHOR; unit-denominated ledger entries move it. Keeping the anchor
        # untouched is what makes a mistyped redemption reversible by deleting its entry.
        moved = sum(float(c.get("units", 0) or 0) for c in (inv.contributions or []))
        # The anchor is a STARTING point that the ledger moves from. Once the ledger is the complete
        # history it already reconstructs the whole holding, so adding the anchor would count every
        # unit twice — the same defect as inventing monthly instalments on top of recorded ones, in
        # the other currency. Caught only because the imported total came out at exactly 2x the broker.
        base = 0.0 if inv.ledger_is_authoritative else float(m["units"])
        units = round(max(0.0, base + moved), 4)
        nav, nav_date = _refresh_nav(inv, db, force_nav)
        months = accrue._months(inv.as_of_date, today)
        # SIPs invested since the anchor add money each month (valued at cost until you re-anchor units);
        # the anchored units carry the market gain. So BOTH contributed and value grow monthly — same as RD/EPF.
        # An authoritative ledger already CONTAINS every instalment, so inventing them from the
        # instruction would count the same money twice — in the invested base and in the XIRR flows.
        invent = inv.active and not inv.ledger_is_authoritative
        new_money = round(max(0.0, inv.monthly_contribution * months - skip_total), 2) if invent else 0.0
        adhoc = sum(float(c.get("amount", 0)) for c in (inv.contributions or []))
        holding = round(units * nav, 2) if nav else float(inv.opening_balance)
        invested = round(float(inv.opening_balance) + adhoc + new_money, 2)
        cur = round(holding + new_money, 2)
        st = accrue.AccrualState(
            current_value=cur, total_contributed=invested,
            interest_earned=round(cur - invested, 2),   # market gain on the anchored holding
            months_elapsed=months,
            monthly_inflow=round(inv.monthly_contribution, 2) if (inv.active and inv.from_takehome) else 0.0,
            maturity_value=None, matured=False,
        )
        market_extra = {"scheme_code": m.get("scheme_code"), "units": units,
                        "nav": round(nav, 4) if nav else None, "nav_date": nav_date,
                        "priced": "live" if nav else "manual"}
        # Returns, done honestly (only when the value is REAL — live NAV):
        #  • return_pct = simple absolute return (value vs invested). Always correct, always shown.
        #  • xirr_pct  = money-weighted annualised return, but ONLY once the holding is old enough
        #    (≥90d) to annualise meaningfully — annualising an 11-day blip is noise (Groww does the same).
        if nav and cur and cur > 0:
            invested_total = st.total_contributed or 0.0
            if invested_total > 0:
                market_extra["return_pct"] = round(st.interest_earned / invested_total * 100, 2)
            span_days = (today - inv.as_of_date).days
            if span_days >= 90:
                from services import xirr as _xirr_mod
                flows: list[tuple[date, float]] = []
                if inv.opening_balance:
                    flows.append((inv.as_of_date, -float(inv.opening_balance)))
                for c in (inv.contributions or []):
                    cd, amt = c.get("date"), float(c.get("amount", 0) or 0)
                    if amt:
                        flows.append((date.fromisoformat(cd[:10]) if cd else inv.as_of_date, -amt))
                if invent and inv.monthly_contribution:
                    for k in range(1, months + 1):
                        y, mo = divmod((inv.as_of_date.month - 1) + k, 12)
                        d_k = date(inv.as_of_date.year + y, mo + 1, min(inv.as_of_date.day, 28))
                        if d_k <= today:                      # never a future-dated contribution
                            flows.append((d_k, -float(inv.monthly_contribution)))
                flows.append((today, float(cur)))
                r = _xirr_mod.xirr(flows)
                if r is not None and -0.95 < r < 10:          # reject degenerate/unrealistic roots
                    market_extra["xirr_pct"] = round(r * 100, 1)
    else:
        # skips → negative ad-hoc contributions at their month, so accrue drops both value and contributed
        # by exactly the money that never went in.
        _contribs = list(inv.contributions or [])
        for s in skips:
            amt = float(s.get("amount", 0) or 0)
            if amt and s.get("month"):
                _contribs.append({"date": f"{s['month']}-15", "amount": -amt, "note": "skipped"})
        st = accrue.accrue(
            opening_balance=inv.opening_balance,
            as_of=inv.as_of_date,
            today=today,
            monthly_contribution=inv.monthly_contribution,
            employer_contribution=inv.employer_contribution,
            annual_rate_pct=inv.annual_rate_pct,
            compounding=inv.compounding,
            contributions=_contribs,
            maturity_date=inv.maturity_date,
            # An authoritative ledger already holds every instalment, so `accrue` must not add the
            # monthly instruction on top of it — `active=False` is how it is told to stop inventing.
            # The instruction itself is unchanged: takehome_outflow below still reports what leaves
            # the bank, because that is a fact about the SIP, not about the ledger.
            active=inv.active and not inv.ledger_is_authoritative,
        )
    return {
        "id": inv.id,
        "name": inv.name,
        "kind": inv.kind,
        "purpose": inv.purpose or "wealth",   # wealth | donation — what the money is FOR
        "holder": inv.holder,
        "opening_balance": inv.opening_balance,
        "as_of_date": inv.as_of_date.isoformat(),
        "monthly_contribution": inv.monthly_contribution,
        "employer_contribution": inv.employer_contribution,
        "annual_rate_pct": inv.annual_rate_pct,
        "compounding": inv.compounding,
        "maturity_date": inv.maturity_date.isoformat() if inv.maturity_date else None,
        "contributions": inv.contributions or [],
        "skips": skips,
        "active": inv.active,
        "from_takehome": inv.from_takehome,
        "category": KIND_CATEGORY.get(inv.kind, "Other"),
        "notes": inv.notes,
        # computed
        "current_value": st.current_value,
        "total_contributed": st.total_contributed,
        "interest_earned": st.interest_earned,
        "months_elapsed": st.months_elapsed,
        "monthly_inflow": st.monthly_inflow,
        # only what actually debits the bank THIS month (drives the monthly engine) — minus any skip.
        "takehome_outflow": round(max(0.0, inv.monthly_contribution - skip_now), 2) if (inv.active and not st.matured and inv.from_takehome) else 0.0,
        "maturity_value": st.maturity_value,
        "matured": st.matured,
        **market_extra,
    }


# Flat market fields folded into the `market` JSON blob (not columns).
_MARKET_FLAT = ("scheme_code", "units", "current_value")


def _apply_defaults(body: RecurringIn) -> dict:
    d = KIND_DEFAULTS.get(body.kind, {})
    data = body.model_dump()
    if data.get("annual_rate_pct") is None:
        data["annual_rate_pct"] = d.get("annual_rate_pct", 0.0)
    if data.get("compounding") is None:
        data["compounding"] = d.get("compounding", "annual")
    for k in _MARKET_FLAT:
        data.pop(k, None)
    return data


def _pack_market(body: RecurringIn, existing: dict | None) -> dict | None:
    """Build/preserve the market blob. Accepts units directly, or backs them out from a
    given current_value ÷ live NAV (so you can add a fund by value, not units)."""
    if not body.scheme_code:
        return None
    units = body.units
    if units is None and body.current_value:
        nav = mf_nav.fetch_nav(body.scheme_code)
        if nav and nav[0]:
            units = round(body.current_value / nav[0], 4)
    if units is None:
        return None
    m = dict(existing or {})
    m["scheme_code"] = str(body.scheme_code)
    m["units"] = units
    return m  # last_nav/nav refreshed lazily on read


def compute_recurring(db: Session, household_id: int | None = None, refresh: bool = False) -> dict:
    """Investment list + computed values + totals. Plain helper so internal callers (cashflow,
    networth) can use it directly; the route wraps it with the current household.
    household_id=None = all."""
    today = date.today()
    q = db.query(RecurringInvestment)
    if household_id is not None:
        q = q.filter(RecurringInvestment.household_id == household_id)
    rows = [_view(r, today, db, force_nav=refresh) for r in q.all()]
    db.commit()  # persist any refreshed NAVs
    total = round(sum(r["current_value"] for r in rows), 2)
    contributed = round(sum(r["total_contributed"] for r in rows), 2)
    by_kind: dict[str, float] = {}
    for r in rows:
        by_kind[r["kind"]] = round(by_kind.get(r["kind"], 0.0) + r["current_value"], 2)
    takehome = round(sum(r["takehome_outflow"] for r in rows), 2)
    inflow = round(sum(r["monthly_inflow"] for r in rows), 2)
    # Money earmarked to leave (a donation RD) is a real bank debit, so it belongs in takehome_outflow
    # and in the cash-flow waterfall. It is NOT wealth: it never lands on the balance sheet, and
    # counting it as invested overstates the corpus and skews the debt/equity ratio.
    donation = round(sum(r["takehome_outflow"] for r in rows
                         if r.get("purpose") == "donation"), 2)
    donation_value = round(sum(r["current_value"] for r in rows
                               if r.get("purpose") == "donation"), 2)
    return {
        "investments": rows,
        "total_value": total,
        "total_contributed": contributed,
        "total_interest": round(total - contributed, 2),
        # THREE distinct monthly numbers — kept separate so no view conflates them:
        "takehome_outflow": takehome,                 # leaves your bank account (SIP/RD/PPF)
        "at_source_inflow": round(inflow - takehome, 2),  # EPF: deducted pre-salary + employer match
        "monthly_inflow": inflow,                     # grand total into all pots (= takehome + at_source)
        # What is actually building wealth — takehome_outflow less anything already spoken for.
        "wealth_outflow": round(takehome - donation, 2),
        "donation_outflow": donation,                 # committed to give away, never wealth
        "total_wealth_value": round(total - donation_value, 2),
        "by_kind": by_kind,
    }


@router.get("/recurring")
def list_recurring(refresh: bool = False, db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)):
    return compute_recurring(db, household.id, refresh=refresh)


@router.get("/recurring/schemes")
def search_schemes(q: str = "", household: Household = Depends(get_current_household)):
    """Fund-name → AMFI scheme code, for picking a scheme when adding or pricing a holding.
    A search box must never 500, so a dead feed and a too-short query both mean no results."""
    if len(q.strip()) < 2:
        return {"schemes": []}
    return {"schemes": mf_nav.search_schemes(q.strip(), direct_growth_only=True)}


def _owned_recurring(db: Session, inv_id: int, household: Household) -> RecurringInvestment:
    inv = db.query(RecurringInvestment).filter(RecurringInvestment.id == inv_id,
                                               RecurringInvestment.household_id == household.id).first()
    if not inv:
        raise HTTPException(404, "Investment not found")
    return inv


@router.post("/recurring")
def create_recurring(body: RecurringIn, db: Session = Depends(get_db),
                     household: Household = Depends(get_current_household)):
    inv = RecurringInvestment(household_id=household.id, **_apply_defaults(body),
                              market=_pack_market(body, None))
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return _view(inv, date.today(), db)


@router.put("/recurring/{inv_id}")
def update_recurring(inv_id: int, body: RecurringIn, db: Session = Depends(get_db),
                     household: Household = Depends(get_current_household)):
    inv = _owned_recurring(db, inv_id, household)
    for k, v in _apply_defaults(body).items():
        setattr(inv, k, v)
    inv.market = _pack_market(body, inv.market)
    db.commit()
    db.refresh(inv)
    return _view(inv, date.today(), db)


@router.post("/recurring/{inv_id}/pause")
def pause_recurring(inv_id: int, active: bool = False, db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    """One-tap pause/resume of a recurring investment — stops (or restarts) its monthly take-home
    outflow without touching any other field. Used by the over-budget recovery levers."""
    inv = _owned_recurring(db, inv_id, household)
    inv.active = active
    db.commit()
    db.refresh(inv)
    return _view(inv, date.today(), db)


@router.post("/recurring/{inv_id}/skip")
def skip_recurring(inv_id: int, body: SkipIn, db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)):
    """Skip PART of a month's contribution (default: this month) — frees exactly that much cash without
    pausing the whole SIP. Capped at the monthly contribution; amount 0 removes the skip. One skip per
    month (re-posting replaces it)."""
    inv = _owned_recurring(db, inv_id, household)
    month = body.month or date.today().strftime("%Y-%m")
    amount = round(max(0.0, min(float(body.amount), float(inv.monthly_contribution or 0.0))), 2)
    skips = [s for s in (inv.skips or []) if s.get("month") != month]   # replace this month's skip
    if amount > 0:
        skips.append({"month": month, "amount": amount})
    inv.skips = skips
    db.commit()
    db.refresh(inv)
    return _view(inv, date.today(), db)


@router.post("/recurring/{inv_id}/contribute")
def add_contribution(inv_id: int, body: ContributeIn, db: Session = Depends(get_db),
                     household: Household = Depends(get_current_household)):
    """Record an ad-hoc lump sum or a redemption, in rupees or in units.

    A NEGATIVE amount (or negative units) is money coming OUT — the only way to tell the app a
    redemption happened."""
    inv = _owned_recurring(db, inv_id, household)
    if (body.amount is None) == (body.units is None):
        raise HTTPException(400, "Give exactly one of amount or units")

    priced: dict = {}
    if body.units is not None:
        if not body.units:
            raise HTTPException(400, "Units must be non-zero")
        scheme_code = (inv.market or {}).get("scheme_code")
        if not scheme_code:
            raise HTTPException(400, "Units need a scheme code on this investment — add one, or log rupees")
        quote = mf_nav.fetch_nav_on(scheme_code, body.date)
        if not quote:
            raise HTTPException(400, f"No NAV available for {body.date.isoformat()}")
        amount = round(body.units * quote[0], 2)
        priced = {"units": body.units, "nav": quote[0], "nav_date": quote[1]}
    else:
        amount = body.amount
        if not amount:
            raise HTTPException(400, "Amount must be non-zero")

    src = (body.funded_from or "bank").lower()
    if src not in ("wallet", "bank", "other"):
        raise HTTPException(400, "funded_from must be wallet, bank or other")
    # A NEGATIVE contribution is a withdrawal FROM the investment — money coming back, not money
    # going out. Taking that from the wallet would deduct for a payment that never happened.
    if src == "wallet" and amount < 0:
        raise HTTPException(400, "A withdrawal cannot be funded from the wallet")
    if src == "wallet":
        from services import wallet
        try:
            wallet.spend(db, household.id, amount, on=body.date.isoformat(),
                         recurring_id=inv.id, note=body.note or f"contribution to {inv.name}")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    log = list(inv.contributions or [])
    # `direction` states what happened in words. The sign is the caller's and stays untouched;
    # this is what makes a redemption readable rather than "a top-up with a minus in front".
    log.append({"amount": amount, "date": body.date.isoformat(), "note": body.note or "",
                "funded_from": src, "direction": "redeem" if amount < 0 else "buy", **priced})
    log.sort(key=lambda c: c.get("date", ""))
    inv.contributions = log
    db.commit()
    db.refresh(inv)
    out = _view(inv, date.today(), db)
    if src == "wallet":
        from services import wallet
        out["wallet_balance"] = wallet.balance(db, household.id)
    return out


@router.delete("/recurring/{inv_id}/contribute/{index}")
def delete_contribution(inv_id: int, index: int, db: Session = Depends(get_db),
                        household: Household = Depends(get_current_household)):
    """Remove one ledger entry. Without this a mistyped redemption is permanent, which is why
    people stop recording them at all."""
    inv = _owned_recurring(db, inv_id, household)
    log = list(inv.contributions or [])
    if not 0 <= index < len(log):
        raise HTTPException(404, "Contribution not found")
    log.pop(index)
    inv.contributions = log
    db.commit()
    db.refresh(inv)
    return _view(inv, date.today(), db)


@router.delete("/recurring/{inv_id}")
def delete_recurring(inv_id: int, db: Session = Depends(get_db),
                     household: Household = Depends(get_current_household)):
    inv = _owned_recurring(db, inv_id, household)
    db.delete(inv)
    db.commit()
    return {"deleted": inv_id}
