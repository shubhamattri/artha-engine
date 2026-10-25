"""Windfall — one lump sum, shown as BOTH "prepay the debt" and "invest it". It never picks.

    GET    /windfall?amount=<inr>&horizon_months=<n>   both optional
    GET    /windfall/measured-return                   the return measured elsewhere, if any
    PUT    /windfall/measured-return                   record one
    DELETE /windfall/measured-return                   forget it

`amount` omitted → the household's investable cash. `horizon_months` omitted → a STATED default.

Two things this module refuses to do, because each has already cost this app something:

  It does not re-implement amortisation. Every rupee of interest here comes from
  `api/debt.payoff_simulation`, the same simulation the Debt tab's payoff plan is drawn from, run
  twice: once as the plan already runs, and once with the windfall paid in today. A parallel
  implementation drifts, and the drift looks like a finding.

  It does not pretend the windfall and the monthly surplus are the same rupees. They are TWO
  STREAMS. The surplus already goes at the debt every month; the windfall is the only thing being
  decided. Measuring against a no-surplus baseline (every loan at its contracted EMI to full term)
  credited the windfall with Rs 60 lakh of interest the surplus was going to avoid anyway.

  It does not answer the invest side with one number from one assumed rate. A hardcoded 12%/yr is
  how a portfolio here got reported 41% above reality, and it is the reason this endpoint exists. The
  invest side is a RANGE across several stated rates, each carrying its own assumption, anchored on
  the household's OWN realised XIRR (api/recurring) rather than an invented figure.

  It does not let the horizon fall out of the data. It used to run to `max(remaining life of any
  loan)`, which an interest-only gold loan can push past 30 years. Projecting equity that far forward
  printed a crore-scale figure next to a prepayment figure:
  arithmetically fine, and meaningless as a comparison. The window is now the household's CHOICE,
  with a stated default, and BOTH sides are worked out inside it.

The two sides are NOT the same kind of number and the response says so in a field, not in prose:
interest avoided is contracted, investment return is assumed. `certainty.comparable` is false, and
nothing here ranks, scores or recommends. It is a calculator.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from api.deps import get_current_household
from config import settings
from database import get_db
from fastapi import APIRouter, Body, Depends, HTTPException
from models.tenant import Household
from sqlalchemy.orm import Session

router = APIRouter()
logger = logging.getLogger(__name__)

# The window both sides are worked out over when nobody has chosen one. Ten years is STATED, not
# derived: it means the same thing for every household, whereas "the longest loan" meant 32 years for
# this one and 4 for the next, so no two readings of this page compared the same thing.
DEFAULT_HORIZON_MONTHS = 120
# What the screen offers. Published rather than hardcoded in the UI, so the window a figure was
# computed over and the window a person can pick can never drift apart.
HORIZON_CHOICES_MONTHS = (12, 36, 60, 120, 180, 240)
MIN_HORIZON_MONTHS = 1
MAX_HORIZON_MONTHS = 360
# A return typed in by a human is a photograph, not a feed. After this long it is reported stale —
# still used, because dropping the household's only measured figure would be worse, but visibly old.
STALE_AFTER_DAYS = 180
# Stated rates for the invest ladder. Not forecasts and not fitted to anything — they exist so the
# answer is a band the household reads across, instead of a single figure it reads as a promise.
ASSUMED_RATES_PCT = (6.0, 9.0, 12.0)
# Months of living costs kept back before cash counts as investable — the same buffer api/afford
# holds when it funds a down payment (api/afford.py:91).
EMERGENCY_MONTHS = 6

_DISCLAIMER = ("Every figure on this page is a projection, not financial advice. The prepay side is "
               "certain only insofar as the contracted rates hold; the invest side is an assumption "
               "and the outcome may fall outside the range shown.")


def _interest_within(cum: list[float], horizon: int) -> float:
    """Interest accrued inside the first `horizon` months of a simulation. Slicing the cumulative
    series the simulator already recorded, rather than running a second, shorter simulation."""
    if not cum:
        return 0.0
    return cum[min(horizon, len(cum)) - 1]


def _fv_lump(pv: float, r: float, n: int) -> float:
    return pv * (1 + r) ** max(0, n)


def _fv_annuity(pmt: float, r: float, n: int) -> float:
    """Month-end contributions, compounded. n floors at 0: a horizon shorter than the payoff has no
    post-clearance months, and a negative count would compound into nonsense."""
    n = max(0, int(n))
    if pmt <= 0 or n == 0:
        return 0.0
    return pmt * (((1 + r) ** n - 1) / r) * (1 + r) if r else pmt * n


def net_position(*, amount: float, emi: float, surplus: float, months_baseline: int,
                 months_with_lump: int, emi_freed: float, horizon_months: int,
                 rate_pct: float) -> dict:
    """The two paths measured the SAME way: what each leaves invested at the same horizon, at the same
    rate, with every rupee of freed commitment put to work on both sides.

    Interest-avoided against compound-growth was never a comparison. It gave the invest arm ten years
    of compounding and the prepay arm one interest figure, with the EMI that prepaying frees counted
    nowhere, which made prepaying look like it lost by a wide margin.

    Nothing here picks. Which side is larger flips with the rate, and that IS the finding.
    """
    r = rate_pct / 100.0 / 12.0
    freed_all = emi + surplus          # once the debt is gone the whole commitment is investable

    # Arm A: the windfall is invested from today, and the surplus keeps clearing the debt on the plan's
    # own schedule, so the commitment only frees up at months_baseline.
    invest = (_fv_lump(amount, r, horizon_months)
              + _fv_annuity(freed_all, r, horizon_months - months_baseline))

    # Arm B: the windfall goes to the debt so it never grows — but the EMI it clears is investable from
    # today, and the debt ends earlier, which is where most of this arm comes from.
    prepay = (_fv_lump(_fv_annuity(emi_freed, r, min(months_with_lump, horizon_months)),
                       r, horizon_months - months_with_lump)
              + _fv_annuity(freed_all, r, horizon_months - months_with_lump))

    return {
        "rate_pct": rate_pct,
        "horizon_months": horizon_months,
        "basis": ("what each path leaves invested at the same horizon and the same rate, counting the "
                  "freed commitment on both sides. A projection, not a forecast."),
        "invest": {
            "value_inr": round(invest, 2), "horizon_months": horizon_months,
            "what": (f"the windfall invested for {horizon_months} months, plus {freed_all:,.0f} a month "
                     f"from month {months_baseline} when the debt clears"),
        },
        "prepay": {
            "value_inr": round(prepay, 2), "horizon_months": horizon_months,
            "what": (f"{emi_freed:,.0f} a month invested from today because the lump clears those loans, "
                     f"then {freed_all:,.0f} a month from month {months_with_lump}"),
        },
        "difference_inr": round(prepay - invest, 2),
        "larger": "prepay" if prepay > invest else "invest" if invest > prepay else "level",
    }


def _attackable(loans: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split the loan list exactly as api/debt.compute_debt does (debt.py:495-510): serviced loans
    are the household's own cash-flow, and a loan whose beneficiary is external is serviced but
    clears somebody else's principal. foir_exempt is deliberately NOT a filter here — it is the
    lender-eligibility axis, and compute_debt keeps such a loan in its totals and its attack order.
    Returns (attackable, excluded-with-reasons)."""
    excluded = []
    attackable = []
    for ln in loans:
        if ln["closed"] or ln["balance_inr"] <= 0:
            continue
        if not ln.get("serviced_by_household", True):
            excluded.append({"id": ln["id"], "name": ln["name"],
                             "reason": "somebody else pays this loan, so it is not this household's "
                                       "money to put in"})
        elif (ln.get("beneficiary") or "household") == "external":
            excluded.append({"id": ln["id"], "name": ln["name"],
                             "reason": "you service it but the principal is somebody else's — "
                                       "prepaying moves your money onto their balance sheet"})
        else:
            attackable.append(ln)
    # Highest NOMINAL rate first: the contracted headline rate, not the after-tax effective rate the
    # Debt tab also publishes. Two orderings on one screen is two answers to one question.
    attackable.sort(key=lambda ln: (-(ln.get("rate_pct") or 0.0), ln["balance_inr"]))
    return attackable, excluded


def _prepay_path(loans: list[dict], amount: float, horizon: int, extra: float,
                 ctx: dict) -> dict:
    """What the windfall buys, measured against the payoff plan that is ALREADY running.

    TWO STREAMS, not one pot. The monthly surplus goes at the debt every month and api/debt owns that
    plan. The windfall is the only thing being decided here. The baseline was once every loan at its
    contracted EMI to full term — a scenario this household will never run — so the headline credited
    the windfall with interest the surplus was going to avoid anyway. Both arms now have the surplus
    flowing, and the difference between them is what the lump itself is worth.

    Neither arm is simulated here. `api/debt.payoff_simulation` is run twice, with and without the
    lump, so the dates on this page and the dates on the Debt tab come out of one implementation.
    """
    from api.debt import payoff_simulation

    attackable, excluded = _attackable(loans)
    base = payoff_simulation(attackable, extra, 0.0)
    after = payoff_simulation(attackable, extra, amount)

    rows: list[dict] = []
    for ln in attackable:
        b, a = base["by_loan"].get(ln["id"]), after["by_loan"].get(ln["id"])
        if b is None or a is None:
            continue
        rule = b["prepayment_rule"]
        applied = a["lump_applied"]
        if applied <= 0:
            # A lender rule is only worth naming when it actually stood between this loan and the
            # money. A loan the cascade simply never reached is not a refusal, and saying so would be
            # inventing a reason.
            if rule == "none":
                excluded.append({"id": ln["id"], "name": ln["name"],
                                 "reason": "this lender refuses early money entirely"})
            elif rule == "foreclose_only":
                excluded.append({"id": ln["id"], "name": ln["name"],
                                 "reason": f"this lender takes nothing short of the whole balance "
                                           f"plus its {ln.get('prepayment_charge_pct') or 0:.1f}% "
                                           f"charge — a part payment would be handed back"})
            continue
        # closes_in_months 0 = cleared today by the lump itself. None = still owing at the end of the
        # simulation horizon, which is a fact about the loan, not a number to fill in.
        before_m, after_m = b["closes_in_months"], a["closes_in_months"]
        cleared = after_m == 0
        rows.append({
            "id": ln["id"], "name": ln["name"], "loan_type": b["loan_type"],
            "rate_pct": b["rate_pct"], "balance_inr": b["balance_inr"],
            "emi_inr": b["emi_inr"], "applied_inr": round(applied, 2), "cleared": cleared,
            "months_before": before_m, "months_after": after_m,
            "months_removed": (None if before_m is None or after_m is None else before_m - after_m),
            "interest_before_inr": round(_interest_within(b["interest_cum"], horizon), 2),
            "interest_after_inr": round(_interest_within(a["interest_cum"], horizon), 2),
            "interest_avoided_inr": round(_interest_within(b["interest_cum"], horizon)
                                          - _interest_within(a["interest_cum"], horizon), 2),
            "interest_avoided_full_term_inr": round(b["interest"] - a["interest"], 2),
            # An EMI is contractual: a part payment shortens the tenure, it does not lower the
            # instalment. Only a loan that actually closes hands its EMI back.
            "emi_freed_inr": round(b["emi_inr"] or 0.0, 2) if cleared else 0.0,
            "amortises": ln["name"] not in base["never_amortises"],
        })

    base_h = _interest_within(base["interest_cum"], horizon)
    after_h = _interest_within(after["interest_cum"], horizon)
    plan_note = ("the payoff plan already running: this household's monthly surplus going at the "
                 "debt, highest nominal rate first, with no windfall in it")
    return {
        "certainty": "certain",
        "basis": "the contracted rate on money already owed — the only thing that moves this figure "
                 "is the lender changing the rate",
        "order": "highest nominal rate first",
        # The window this side was counted over. Same number the invest side uses, so the two figures
        # on screen cover the same months.
        "horizon_months": horizon,
        "horizon_note": "interest avoided is counted inside the chosen window; the whole remainder is "
                        "published alongside it as interest_avoided_full_term_inr",
        # Which surplus produced these numbers, said out loud. `surplus` is what the household could
        # redirect if it holds its budget; `actionable` is what is safe to move THIS cycle once an
        # overspend is taken off it. The plan uses the first, so this does too — a windfall page
        # running on a different surplus from the Debt tab would tell one household two payoff
        # stories.
        "surplus": {
            "assumed_per_month_inr": round(extra, 2),
            "field": "strategies.plan_extra",
            "source": "api/debt — the same monthly surplus the payoff plan on the Debt tab runs on",
            "why_this_one": ("the plan is projected at `surplus`, what could go in if the budget "
                             "holds, not at `actionable`, what is safe to move this cycle after an "
                             "overspend. Both are published here so the gap is visible"),
            "surplus_inr": ctx.get("surplus"),
            "actionable_inr": ctx.get("actionable"),
            "over_budget_inr": ctx.get("over_budget"),
            "data_ok": bool(ctx.get("data_ok", True)),
        },
        "baseline": {
            "what": plan_note,
            "with_surplus_inr": round(extra, 2),
            "months": base["months"],
            "complete": base["complete"],
            "interest_inr": round(base_h, 2),
            "interest_full_term_inr": base["total_interest"],
        },
        "with_windfall": {
            "what": "the same plan, same surplus, with the windfall paid in today",
            "months": after["months"],
            "complete": after["complete"],
            "interest_inr": round(after_h, 2),
            "interest_full_term_inr": after["total_interest"],
        },
        "amount_applied_inr": round(sum(r["applied_inr"] for r in rows), 2),
        "amount_unused_inr": after["lump_unused"],
        "prepayment_fees_inr": after["lump_fees"],
        "interest_avoided_inr": round(base_h - after_h, 2),
        "interest_avoided_full_term_inr": round(
            base["total_interest"] - after["total_interest"], 2),
        "emi_freed_per_month_inr": round(sum(r["emi_freed_inr"] for r in rows), 2),
        "months_removed": base["months"] - after["months"],
        # The payoff plan's own length, with and without the windfall. Not the projection window —
        # that is `horizon_months`, and conflating the two is what put 384 months on this page.
        "horizon_months_before": base["months"],
        "horizon_months_after": after["months"],
        "loans": rows,
        "cleared": [r["name"] for r in rows if r["cleared"]],
        "considered": [ln["id"] for ln in attackable],
        "excluded": excluded,
        # Named out loud: a loan whose payment never clears its own interest has no knowable remaining
        # interest, so the total above is a floor rather than the whole picture.
        "interest_incomplete": list(base["never_amortises"]),
    }


def _realised_xirr(db: Session, household_id: int | None) -> tuple[float | None, str]:
    """The household's OWN money-weighted return, value-weighted across every holding api/recurring
    has measured one for. Returns (rate_pct, assumption-or-reason)."""
    try:
        from api.recurring import compute_recurring
        rows = compute_recurring(db, household_id)["investments"]
    except Exception:  # noqa: BLE001 — a recurring failure must not take down the page
        logger.exception("windfall: could not read realised returns")
        return None, "the investments module could not be read, so no measured return is available"
    measured = [r for r in rows if r.get("xirr_pct") is not None and (r.get("current_value") or 0) > 0]
    if not measured:
        return None, ("no holding here has both a live price and at least 90 days of history yet, so "
                      "this household has no realised XIRR to anchor on — nothing has been invented "
                      "in its place")
    weight = sum(r["current_value"] for r in measured)
    rate = sum(r["xirr_pct"] * r["current_value"] for r in measured) / weight
    return round(rate, 1), (
        f"observed: the money-weighted return (XIRR) this household has actually realised on "
        f"{len(measured)} market holding(s), value-weighted across them and measured by api/recurring. "
        f"It is a measurement of the past, not a forecast of the future.")


def _measured_entry(household: Household | None) -> dict | None:
    """The return this household measured somewhere else and typed in. None when nothing was entered
    — and nothing is ever substituted for it."""
    if household is None or household.measured_return_pct is None:
        return None
    as_of = household.measured_return_as_of
    age = (date.today() - as_of).days if as_of else None
    entered = household.measured_return_entered_at
    return {
        "rate_pct": round(float(household.measured_return_pct), 2),
        "scope": household.measured_return_scope,
        "as_of": as_of.isoformat() if as_of else None,
        "entered_at": entered.isoformat() if entered else None,
        "as_of_days_ago": age,
        "stale": bool(age is not None and age > STALE_AFTER_DAYS),
        "stale_after_days": STALE_AFTER_DAYS,
    }


def _invest_path(db: Session, household_id: int | None, household: Household | None,
                 amount: float, months: int, debt_baseline: dict, surplus: float) -> dict:
    scenarios: list[dict] = []
    seen: set[float] = set()

    def add(key: str, label: str, rate_pct: float, source: str, assumption: str,
            observed_from: str | None = None, as_of: str | None = None,
            stale: bool = False) -> None:
        r = round(rate_pct, 1)
        if r in seen:
            return
        seen.add(r)
        value = round(amount * (1.0 + r / 100.0) ** (months / 12.0), 2)
        scenarios.append({"key": key, "label": label, "rate_pct": r, "source": source,
                          # `source` says measured-vs-assumed. `observed_from` says WHO measured it,
                          # because "Artha worked this out from your holdings" and "a human read it
                          # off a broker screen" are not the same claim and must not read alike.
                          "observed_from": observed_from, "as_of": as_of, "stale": stale,
                          "assumption": assumption, "value_inr": value,
                          "gain_inr": round(value - amount, 2)})

    rate, why = _realised_xirr(db, household_id)
    entry = _measured_entry(household)
    # PRECEDENCE, stated out loud: a figure this app can derive and re-derive beats one it cannot
    # check. So the moment redemption history exists and api/recurring returns an XIRR, the derived
    # anchor is used and the hand-entered one is marked superseded rather than silently dropped.
    if rate is not None:
        add("realised_xirr", "Measured by Artha, from your own holdings", rate, "observed", why,
            observed_from="derived_in_app")
    elif entry is not None:
        age = entry["as_of_days_ago"]
        stale_note = ("" if not entry["stale"] else
                      f" This figure is {age} days old, past the {STALE_AFTER_DAYS}-day mark, so it "
                      f"is stale: read it again from the source and re-enter it.")
        add("measured_elsewhere", "Measured elsewhere, entered by hand", entry["rate_pct"],
            "observed",
            f"measured, but not by this app: {entry['scope']}, as of {entry['as_of']}"
            + (f" ({age} days ago)" if age is not None else "")
            + f", typed in by hand. Artha cannot check it — {why}"
              f" It is a measurement of the past, not a forecast." + stale_note,
            observed_from="entered_by_hand", as_of=entry["as_of"], stale=entry["stale"])
    equity = settings.equity_return * 100.0
    add("equity_assumption", f"{equity:.1f}% a year", equity, "assumed",
        f"assumed: the long-run equity return this app plans with (config `equity_return` = "
        f"{equity:.1f}% a year). An assumption about markets, not a forecast.")
    for r in ASSUMED_RATES_PCT:
        add(f"assumed_{r:g}", f"{r:.1f}% a year", r, "assumed",
            f"assumed: a stated {r:.1f}% a year held flat for the whole horizon. It is here to give "
            f"the answer a width; it is not a forecast and the outcome may land outside this range.")
    scenarios.sort(key=lambda s: s["rate_pct"])

    observed = [s for s in scenarios if s["source"] == "observed"]
    lo, hi = scenarios[0], scenarios[-1]
    measured = None
    if entry is not None:
        measured = dict(entry, in_use=rate is None, superseded_by_derived=rate is not None,
                        # Why the app could not derive it itself. The reason survives even when the
                        # typed figure is doing the anchoring, because it is what makes the typed
                        # figure necessary.
                        why_not_derived=None if rate is not None else why)
    return {
        "certainty": "uncertain",
        "basis": "a projection at several stated rates. None of them is a forecast, and no single "
                 "rate is used as the answer",
        "method": f"amount x (1 + rate) ^ ({months}/12). No further contributions, no tax, no fees.",
        "horizon_months": months,
        "single_rate_used": False,
        # Investing the windfall does not switch the debt off. The monthly surplus keeps clearing it
        # on exactly the schedule the prepay side calls its baseline, so both arms of this page have
        # the same money doing the same thing except for the lump.
        "while_invested": {
            "surplus_per_month_inr": round(surplus, 2),
            "debt_months": debt_baseline["months"],
            "debt_complete": debt_baseline["complete"],
            "note": "the monthly surplus keeps going at the debt on the plan's own schedule; only "
                    "the windfall is invested here",
        },
        "scenarios": scenarios,
        "range": {"low_rate_pct": lo["rate_pct"], "high_rate_pct": hi["rate_pct"],
                  "low_value_inr": lo["value_inr"], "high_value_inr": hi["value_inr"],
                  "low_gain_inr": lo["gain_inr"], "high_gain_inr": hi["gain_inr"]},
        "measured_return": measured,
        # Set only when there is NO observed anchor at all. With one, the page is not missing a
        # measurement, and `measured_return.why_not_derived` still carries the reason.
        "realised_xirr_unavailable_why": None if observed else why,
    }


def _investable_cash(db: Session, household_id: int | None) -> dict:
    """Cash on the balance sheet, less the emergency buffer. Both halves come from the modules that
    OWN them — api/networth for asset values, api/cashflow for the monthly commitments the buffer is
    six months of — so neither is recomputed here."""
    try:
        from api.cashflow import compute_cashflow
        from api.networth import compute_assets
        from services.disposals import outstanding_receivables
        cash = float(compute_assets(db, household_id)["by_type"].get("cash", 0.0))
        # Net worth counts a sale receivable, correctly — it is owed and it is his. It is still not
        # spendable until registration, so it cannot be offered as money to deploy today.
        # Its own try: a disposals failure must not take the whole cash figure down with it, and
        # `None` says "not measured" rather than claiming a confident zero.
        try:
            recv = round(sum(float(r["value_inr"])
                             for r in outstanding_receivables(db, household_id)), 2)
            cash = round(max(0.0, cash - recv), 2)
        except Exception:  # noqa: BLE001
            recv = None
        cf = compute_cashflow(db, household_id)
        buffer_inr = round(EMERGENCY_MONTHS * (cf["fixed"] + cf["leftover"]), 2)
        return {"amount_inr": round(max(0.0, cash - buffer_inr), 2),
                "liquid_cash_inr": round(cash, 2), "receivable_inr": recv,
                "emergency_buffer_inr": buffer_inr,
                "basis": f"cash & bank assets less {EMERGENCY_MONTHS} months of fixed costs plus the "
                         f"variable-spend budget — the same buffer api/afford keeps back",
                "data_ok": True}
    except Exception:  # noqa: BLE001 — degrade visibly rather than publish a confident zero
        logger.exception("windfall: investable cash unavailable")
        return {"amount_inr": 0.0, "liquid_cash_inr": None, "receivable_inr": None,
                "emergency_buffer_inr": None,
                "basis": "could not be read", "data_ok": False}


def _years(months: int) -> str:
    if months % 12 == 0:
        y = months // 12
        return f"{y} year" if y == 1 else f"{y} years"
    return f"{months} months"


def _resolve_horizon(horizon_months: int | None) -> tuple[int, str, str]:
    """(months, source, basis). Refused rather than clamped when out of range: silently answering a
    different question than the one asked is how a projection stops meaning anything."""
    if horizon_months is None:
        return DEFAULT_HORIZON_MONTHS, "default", (
            f"a stated default of {_years(DEFAULT_HORIZON_MONTHS)}, the same window for every "
            f"household. It is deliberately not taken from the longest loan — doing that projected "
            f"this page 32 years ahead because one interest-only gold loan runs that long. Change it "
            f"above and both sides move with it")
    try:
        months = int(horizon_months)
    except (TypeError, ValueError):
        raise HTTPException(400, f"horizon_months must be a whole number of months between "
                                 f"{MIN_HORIZON_MONTHS} and {MAX_HORIZON_MONTHS}") from None
    if not MIN_HORIZON_MONTHS <= months <= MAX_HORIZON_MONTHS:
        raise HTTPException(400, f"horizon_months must be between {MIN_HORIZON_MONTHS} and "
                                 f"{MAX_HORIZON_MONTHS}")
    return months, "chosen", (f"the {_years(months)} you chose. Both sides are worked out over it, "
                              f"and nothing here is projected past it")


def compute_windfall(db: Session, household_id: int | None = None,
                     amount: float | None = None,
                     horizon_months: int | None = None) -> dict:
    if amount is not None and amount < 0:
        raise HTTPException(400, "Amount cannot be negative")
    from api.debt import compute_debt

    horizon, horizon_source, basis = _resolve_horizon(horizon_months)
    cash = _investable_cash(db, household_id)
    used = float(amount) if amount is not None else cash["amount_inr"]
    household = db.get(Household, household_id) if household_id is not None else None

    debt = compute_debt(db, household_id)
    strategies = debt.get("strategies") or {}
    # The monthly surplus already going at the debt, taken from api/debt rather than recomputed. It
    # is the same figure the payoff plan is projected at, which is the only way this page and the
    # Debt tab can agree about when the household is debt-free.
    surplus_extra = float(strategies.get("plan_extra") or 0.0)
    prepay = _prepay_path(debt["loans"], used, horizon, surplus_extra,
                          strategies.get("surplus_context") or {})
    invest = _invest_path(db, household_id, household, used, horizon,
                          prepay["baseline"], surplus_extra)

    # The only symmetric reading. Interest-avoided against compound-growth gave one arm ten years of
    # compounding and the other a single interest figure, so it decided the answer by itself.
    base_m = (prepay.get("baseline") or {}).get("months")
    lump_m = (prepay.get("with_windfall") or {}).get("months")
    positions = []
    if base_m and lump_m:
        for s in invest.get("scenarios") or []:
            positions.append({
                **net_position(amount=used, emi=float(debt.get("total_emi") or 0.0),
                               surplus=surplus_extra, months_baseline=int(base_m),
                               months_with_lump=int(lump_m),
                               emi_freed=float(prepay.get("emi_freed_per_month_inr") or 0.0),
                               horizon_months=horizon, rate_pct=float(s["rate_pct"])),
                "source": s.get("source"), "label": s.get("label"),
            })

    return {
        "net_position": {
            "scenarios": positions,
            "why": ("both arms measured the same way at the same horizon, with the freed commitment "
                    "invested on both sides. Which is larger flips with the rate."),
        } if positions else None,
        "amount_inr": round(used, 2),
        "amount_source": "requested" if amount is not None else "investable_cash",
        "investable_cash": cash,
        "horizon_months": horizon,
        "horizon_source": horizon_source,
        "horizon_basis": basis,
        "horizon_choices": [{"months": m, "label": _years(m)} for m in HORIZON_CHOICES_MONTHS],
        "prepay": prepay,
        "invest": invest,
        # The structural difference, machine-readable, so no consumer can render the two figures as
        # one comparison and pick a winner off it.
        "certainty": {
            "prepay": {"kind": "certain", "figure": "prepay.interest_avoided_inr",
                       "why": "interest you do not pay on money you already owe, at a rate already "
                              "contracted"},
            "invest": {"kind": "uncertain", "figure": "invest.range",
                       "why": "a projection at rates that are assumed; the real outcome is unknown "
                              "and may fall outside the range"},
            "comparable": False,
            "comparable_why": "one figure is contracted and one is assumed, so they are not the same "
                              "kind of number and must not be resolved into a single winner",
        },
        "disclaimer": _DISCLAIMER,
    }


@router.get("/windfall")
def get_windfall(amount: float | None = None, horizon_months: int | None = None,
                 db: Session = Depends(get_db),
                 household: Household = Depends(get_current_household)):
    return compute_windfall(db, household.id, amount, horizon_months)


def _measured_view(household: Household) -> dict:
    return {"measured_return": _measured_entry(household),
            "stale_after_days": STALE_AFTER_DAYS}


@router.get("/windfall/measured-return")
def get_measured_return(db: Session = Depends(get_db),  # noqa: ARG001 — symmetry with the writers
                        household: Household = Depends(get_current_household)):
    return _measured_view(household)


@router.put("/windfall/measured-return")
def set_measured_return(payload: dict = Body(...), db: Session = Depends(get_db),
                        household: Household = Depends(get_current_household)):
    """Record a return the household measured somewhere this app cannot see — a broker's realised
    XIRR, typed in.

    Every field is required, and that is the whole safety of the thing. A rate with no date and no
    scope is the bare number this app refuses to render anywhere: it cannot be aged, cannot be
    checked against its source, and reads as a fact forever."""
    raw = payload.get("rate_pct")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        try:
            raw = float(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, "rate_pct must be a number, e.g. 10.43") from None
    rate = float(raw)
    # Negative is legitimate — a household can genuinely have lost money, and rounding that up to
    # zero would be the same lie as inventing a return. Absurd is refused.
    if not -100.0 <= rate <= 100.0:
        raise HTTPException(400, "rate_pct must be between -100 and 100")

    scope = (payload.get("scope") or "").strip()
    if not scope:
        raise HTTPException(400, "scope is required — say what this return refers to, e.g. "
                                 "'every equity holding at the broker'")

    raw_as_of = payload.get("as_of")
    if not raw_as_of:
        raise HTTPException(400, "as_of is required — the date this figure is measured to")
    try:
        as_of = date.fromisoformat(str(raw_as_of))
    except ValueError:
        raise HTTPException(400, "as_of must be a date, YYYY-MM-DD") from None
    if as_of > date.today():
        raise HTTPException(400, "as_of cannot be in the future — nothing has been measured yet")

    household.measured_return_pct = rate
    household.measured_return_scope = scope
    household.measured_return_as_of = as_of
    household.measured_return_entered_at = datetime.utcnow()
    db.add(household)
    db.commit()
    return _measured_view(household)


@router.delete("/windfall/measured-return")
def clear_measured_return(db: Session = Depends(get_db),
                          household: Household = Depends(get_current_household)):
    """Forget it entirely. The page then goes back to having no observed anchor and saying why —
    never to a default standing in for a measurement."""
    household.measured_return_pct = None
    household.measured_return_scope = None
    household.measured_return_as_of = None
    household.measured_return_entered_at = None
    db.add(household)
    db.commit()
    return _measured_view(household)
