"""Debt API — loans stored as inputs, everything else computed by loan_math.

Endpoints:
  GET    /debt              list + totals + avalanche payoff plan
  GET    /debt/{id}         full bank-grade detail + amortisation schedule
  POST   /debt             add a loan
  PUT    /debt/{id}        edit a loan
  POST   /debt/{id}/preclose   foreclose, or record a lump prepayment
  DELETE /debt/{id}        remove a loan
"""

from __future__ import annotations

import logging
from datetime import date

from api.deps import get_current_household
from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from models.household import Loan

# Import at MODULE level (loaded by main at startup) so the Loan.payments relationship's "LoanPayment"
# string resolves when mappers configure — a lazy in-function import would leave the FIRST Loan query
# (any /debt, /networth, /cashflow call) raising "failed to locate a name 'LoanPayment'".
from models.loan_payment import LoanPayment  # noqa: F401  (registers the mapper)
from models.tenant import Household
from pydantic import BaseModel
from services import loan_math
from sqlalchemy.orm import Session

router = APIRouter()
logger = logging.getLogger(__name__)

# Simulation horizon. 360 was too short to be a cap and act as a truth-teller at the same time: a gold
# loan serviced at interest-only legitimately needs ~384 months, so the minimums-only BASELINE never
# completed, which nulled `interest_saved` and `months_saved` on EVERY strategy — the whole comparison
# went blank because one loan is slow, not because anything was unknowable. 600 clears any real
# schedule while still terminating a genuinely never-amortising one.
MAX_MONTHS = 600
# Was 120: shorter than a normal home-loan tenure, so the minimums-only baseline could NEVER finish and
# every "saved vs minimums" figure was really a readout of this constant.


# ── Schemas ──────────────────────────────────────────────────
class LoanIn(BaseModel):
    name: str
    lender: str
    holder: str
    category: str
    original_principal: float
    annual_rate_pct: float
    tenure_months: int
    disbursement_date: date
    emi_override: float | None = None
    notes: str | None = None
    foir_exempt: bool = False                  # off the FOIR eligibility ratio + payoff strategies
    serviced_by_household: bool = True         # household pays it → in cash-flow leftover + net-worth liability
    beneficiary: str = "household"             # household | member | external — whose principal is it
    expected_monthly_payment: float | None = None  # gold: nominal ₹/mo for the leftover projection
    # NULL = derive it (services/prepayment: floating + individual is RBI-decided). A non-null value
    # is an explicit override from the sanction letter and always wins. Defaulting this to "part"
    # meant every edit silently wrote an override and turned a foreclose-only loan part-payable.
    prepayment: str | None = None              # part | foreclose_only | none
    prepayment_charge_pct: float | None = None  # % of outstanding, when the lender charges one
    rate_type: str = "fixed"                   # fixed | floating (tenure flexes on rate change)
    loan_type: str = "standard"               # standard | flexi | gold (interest-only bullet + ledger)
    disbursed_amount: float | None = None      # flexi: fallback drawn (if no tranches)
    moratorium_months: int = 0                 # flexi: interest-only window
    moratorium_interest_mode: str | None = None  # full | partial | none
    disbursements: list[dict] | None = None    # flexi: tranches [{amount, date, note?}]


class DisburseIn(BaseModel):
    amount: float
    date: date
    note: str | None = None


class PaymentIn(BaseModel):
    date: date
    amount: float
    note: str | None = None
    kind: str = "extra"            # extra | skipped_emi
    # wallet | bank | other. Defaults to bank so an unstated payment never quietly drains the
    # wallet — a balance that moves without being told to is one you stop trusting.
    funded_from: str | None = None


class SkipIn(BaseModel):
    """A month's EMI that did not happen. Amount defaults to the loan's own EMI."""
    date: date
    amount: float | None = None
    note: str | None = None


class PrecloseIn(BaseModel):
    mode: str = "foreclose"      # "foreclose" (clear it) | "prepay" (lump sum)
    amount: float | None = None  # required for prepay


# ── Helpers ──────────────────────────────────────────────────
# Loan obligation math (state, EMI, flexi/moratorium) lives in ONE shared service so Debt, Cashflow and
# Commitments never disagree on a loan's EMI. These are thin aliases to keep call sites unchanged.
from services import loan_obligation

_is_flexi = loan_obligation.is_flexi
_is_gold = loan_obligation.is_gold
_ft = loan_obligation.flexi_terms
_drawn = loan_obligation.drawn
_state_of = loan_obligation.state_of
_emi_of = loan_obligation.monthly_emi


def _pack_flexi(body: LoanIn) -> dict | None:
    """Build the flexi_terms blob from the flat API fields (None for standard loans)."""
    if body.loan_type != "flexi":
        return None
    return {
        "disbursed_amount": body.disbursed_amount,
        "disbursements": body.disbursements or [],
        "moratorium_months": body.moratorium_months or 0,
        "interest_mode": body.moratorium_interest_mode or "full",
    }


def _closure(today: date, months_left: int) -> tuple[str | None, int]:
    """Projected closure: today + months_left. Returns (iso_date, months_left)."""
    if months_left <= 0:
        return None, 0
    y, m = today.year, today.month + months_left
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    try:
        return date(y, m, min(today.day, 28)).isoformat(), months_left
    except ValueError:
        return date(y, m, 28).isoformat(), months_left


def _view(loan: Loan, today: date) -> dict:
    from services import prepayment as _prepay
    _pp = _prepay.effective(loan)
    st, extra = _state_of(loan, today)
    # ONE EMI truth: the shared obligation service (handles override, flexi, and a gold loan's nominal
    # expected_monthly_payment) — so the Debt tab, Cashflow and Commitments never disagree on the number.
    emi_amt = _emi_of(loan, today)
    rate_type = getattr(loan, "rate_type", "fixed") or "fixed"
    # Floating loans flex TENURE as the rate changes — recompute months-left from the live
    # balance + EMI at the current rate (so a repo/rate edit moves the closure date).
    months_left = st.months_left
    if rate_type == "floating" and not _is_flexi(loan) and st.current_balance > 0 and emi_amt > 0:
        months_left = loan_math.months_remaining(st.current_balance, loan.annual_rate_pct, emi_amt)
    closes_on, closes_in = _closure(today, months_left)
    return {
        "id": loan.id,
        "name": loan.name,
        "lender": loan.lender,
        "holder": loan.holder,
        "category": loan.category,
        "loan_type": loan.loan_type or "standard",
        "phase": extra.get("phase"),
        "post_moratorium_emi": extra.get("post_moratorium_emi"),
        "monthly_interest": extra.get("monthly_interest"),
        "disbursed_amount": _ft(loan).get("disbursed_amount"),
        "disbursements": _ft(loan).get("disbursements") or [],
        "total_disbursed": round(_drawn(loan), 2) if _is_flexi(loan) else None,
        "sanctioned_amount": loan.original_principal,
        "moratorium_months": _ft(loan).get("moratorium_months") or 0,
        # computed (formula-driven)
        "balance_inr": round(st.current_balance, 2),
        "rate_pct": loan.annual_rate_pct,
        "rate_type": rate_type,
        "emi_inr": round(emi_amt, 2),
        "months_remaining": months_left,
        "closes_on": closes_on,
        "closes_in_months": closes_in,
        "months_passed": st.months_passed,
        "original_principal": loan.original_principal,
        "tenure_months": loan.tenure_months,
        "disbursement_date": loan.disbursement_date.isoformat(),
        "principal_paid": round(st.principal_paid, 2),
        "interest_paid": round(st.interest_paid, 2),
        "total_interest_over_life": round(st.total_interest_over_life, 2),
        "next_due": st.next_due.isoformat() if st.next_due else None,
        "progress_pct": round(st.progress_pct, 1),
        "closed": loan.closed,
        "foir_exempt": bool(getattr(loan, "foir_exempt", False)),
        "serviced_by_household": bool(getattr(loan, "serviced_by_household", True)),
        "beneficiary": getattr(loan, "beneficiary", None) or "household",
        "expected_monthly_payment": getattr(loan, "expected_monthly_payment", None),
        "prepayment": getattr(loan, "prepayment", None),          # explicit override, or None
        "prepayment_rule": _pp[0], "prepayment_charge_pct": _pp[1], "prepayment_why": _pp[2],
        # gold-loan account fields (present for loan_type='gold'): live principal vs accrued interest,
        # plus the payment ledger so the UI can show/settle it.
        "principal_outstanding": extra.get("principal_outstanding"),
        "interest_due": extra.get("interest_due"),
        # Returned for EVERY loan, not just gold. Gating it on gold meant a prepayment logged
        # against a car or home loan could be created and never listed or deleted — write-only data
        # the user cannot undo.
        "payments": [{"id": p.id, "date": p.date.isoformat(), "amount": p.amount, "note": p.note,
                      "kind": getattr(p, "kind", None) or "extra"}
                     for p in sorted(getattr(loan, "payments", []) or [], key=lambda x: x.date)],
        "closed_on": loan.closed_on.isoformat() if getattr(loan, "closed_on", None) else None,
        "notes": loan.notes,
    }


# ── payoff strategies ─────────────────────────────────────────────────────────
# Every mainstream debt-payoff method is the SAME month-by-month simulation with a different rule for
# "which loan gets the spare rupee this month". We run each so the user can compare months-to-free and
# total interest, then pick the one that fits (interest-optimal vs quick wins vs cash-flow relief).
# sort_key orders the loans; the focus each month is the first still-open loan in that order.
_STRATEGIES = [
    ("avalanche", "Costliest first", "Pay off the highest interest rate first. Cheapest overall.",
     lambda l: (-l["rate_pct"], l["balance_inr"])),
    ("snowball", "Smallest first", "Pay off the smallest loan first. Quick wins keep you going.",
     lambda l: (l["balance_inr"], -l["rate_pct"])),
    ("highest_balance", "Biggest first", "Pay off the largest loan first. Your biggest debt goes soonest.",
     lambda l: (-l["balance_inr"], -l["rate_pct"])),
    ("cash_flow", "Free up cash", "Pay off the biggest EMI first. Frees the most monthly cash soonest.",
     lambda l: (-l["emi_inr"], -l["rate_pct"])),
]


def _simulate(active: list[dict], sort_key, extra: float, rolldown: bool = True,
              lump: float = 0.0) -> dict:
    """One ordering, simulated to payoff. With `rolldown=True` (the real avalanche/snowball mechanism)
    the focus loan each month gets your spare cash PLUS the EMIs freed by every loan already cleared —
    that money cascades onto the next target. With `rolldown=False` it's a TRUE minimums-only baseline:
    every loan just pays its own EMI, nothing is redirected (used for an honest 'interest saved' figure).
    Returns months-to-free, total interest, per-loan close month, and a step-by-step plan.

    `lump` is a ONE-OFF payment made TODAY, before the first month accrues — a windfall, not a monthly
    habit. It goes down the same cascade as everything else, so it obeys the same lender rules: a
    lender that refuses part payment refuses the windfall too. api/windfall runs this twice, with and
    without the lump, so "interest avoided" is measured against the payoff plan that is ALREADY
    running rather than against a fictional world where the household's surplus does not exist."""
    order = sorted(active, key=sort_key)
    n = len(order)
    # Whether each lender will actually TAKE early money. A plan that part-pays a loan the lender
    # refuses to part-pay promises a closure date that cannot happen.
    prepay = [l.get("prepayment_rule") or "part" for l in order]
    # Cost of closing early, as a fraction of outstanding. Charged ON TOP of the balance, so the pool
    # has to cover both before a foreclose-only loan can actually be cleared.
    prepay_fee = [(l.get("prepayment_charge_pct") or 0.0) / 100.0 for l in order]
    bal = [l["balance_inr"] for l in order]
    rate = [l["rate_pct"] for l in order]
    emis = [l["emi_inr"] for l in order]
    # A moratorium (flexi) loan pays interest-only TODAY and steps up to its full EMI when the
    # moratorium ends (typically a 2–4x jump). Without this the sim assumes the interest-only figure
    # runs for the whole horizon, which can put the payoff date out by several months — worst when the
    # household's spare cash is lowest. A "step" that would LOWER the payment is ignored (stale data).
    step: list[tuple[int, float] | None] = []
    for l in order:
        mor, post, passed = (l.get("moratorium_months") or 0), l.get("post_moratorium_emi"), (l.get("months_passed") or 0)
        step.append((max(1, mor - passed + 1), float(post))
                    if (post and mor and post > (l["emi_inr"] or 0)) else None)
    # Loans whose OWN payment doesn't even cover their monthly interest never shrink on their own —
    # they only clear if the rolldown pool reaches them. Surfaced so the UI can say so out loud.
    # Judged on the payment the loan will EVENTUALLY carry, not today's: a moratorium loan pays
    # interest-only now and steps up later, so testing today's figure named it as never-amortising
    # when it amortises fine from the step. The sim already applies the step; this flag did not, and
    # that single wrong name was enough to blank the savings figures across all four strategies.
    never_amortises = [order[i]["name"] for i in range(n)
                       if max(emis[i], step[i][1] if step[i] else 0.0) <= bal[i] * rate[i] / 1200.0]

    start_bal = list(bal)
    total_interest = 0.0
    fees_paid = 0.0                 # foreclosure charges — a real cost of the plan, not interest
    closed_on_month: list[int | None] = [None] * n
    # The biggest monthly amount each loan actually received, and what it was made of. Recorded from
    # the simulation itself so the on-screen plan can never claim a payment the sim didn't make.
    push = [{"total": 0.0, "own_emi": 0.0, "from_pool": 0.0} for _ in range(n)]
    peak_month: list[int | None] = [None] * n     # the month each loan's headline figure applies from
    first_pool: list[int | None] = [None] * n     # the month the pool FIRST reaches it = the real attack order
    # Interest per loan, and the running total after each month. A cascade makes interest a portfolio
    # outcome — closing one loan early changes when the pool reaches the next — but what each loan
    # itself accrues is still well defined, and the cumulative series is what lets a caller ask "how
    # much of this falls inside the next N months" without a second simulation.
    int_by_loan = [0.0] * n
    interest_cum: list[float] = []
    int_cum_by_loan: list[list[float]] = [[] for _ in range(n)]

    def _cascade(pool: float, month: int) -> tuple[float, float, list[float]]:
        """Spend `pool` down the strategy order, honouring each lender's early-money rule. Shared by
        the month-0 lump and every month's rolldown, so a windfall and a monthly surplus can never end
        up obeying two different sets of lender rules."""
        used = [0.0] * n
        fees = 0.0
        for i in range(n):
            if pool <= 1e-9:
                break
            if bal[i] <= 0:
                continue
            # 'none' never takes early money. 'foreclose_only' takes it only when the pool can clear
            # the WHOLE balance — a part payment would be handed straight back. Either way the cash
            # is not destroyed: it flows to the next loan in the order, which is what a household
            # actually does when a lender says no.
            if prepay[i] == "none":
                continue
            fee = bal[i] * prepay_fee[i]
            if prepay[i] == "foreclose_only":
                if pool < bal[i] + fee:          # cannot afford the balance AND the lender's fee
                    continue
                pool -= fee                       # the fee is real money out, not free
                fees += fee
            use = min(bal[i], pool)
            if use > 0 and first_pool[i] is None:
                first_pool[i] = month
            bal[i] -= use
            pool -= use
            used[i] += use
            if bal[i] <= 0 and closed_on_month[i] is None:
                closed_on_month[i] = month
        return pool, fees, used

    # Month 0: the one-off lump, paid before anything accrues. Deliberately outside `push`/`peak_month`
    # — those describe the MONTHLY plan, and a windfall is not a monthly payment.
    lump_left, lump_used = float(max(0.0, lump)), [0.0] * n
    lump_fees = 0.0
    if lump_left > 0:
        lump_left, lump_fees, lump_used = _cascade(lump_left, 0)
        fees_paid += lump_fees

    months = 0
    for month in range(1, MAX_MONTHS + 1):
        if all(b <= 0 for b in bal):
            break
        months = month
        for i in range(n):                                  # a moratorium ending this month
            if step[i] and month == step[i][0]:
                emis[i] = step[i][1]
        for i in range(n):                                  # accrue this month's interest
            if bal[i] > 0:
                intr = bal[i] * rate[i] / 1200.0
                total_interest += intr
                int_by_loan[i] += intr
                bal[i] += intr
        interest_cum.append(total_interest)
        for i in range(n):
            int_cum_by_loan[i].append(int_by_loan[i])
        # EMIs freed by loans closed in EARLIER months (measured before this month's payments).
        freed = sum(emis[i] for i in range(n) if bal[i] <= 0) if rolldown else 0.0
        month_pay = [0.0] * n
        spare = 0.0
        for i in range(n):                                  # 1) every loan's contractual minimum
            if bal[i] <= 0:
                continue
            pay = min(emis[i], bal[i])                       # never pay more than is owed…
            spare += emis[i] - pay                           # …the unused tail is real money, keep it
            bal[i] -= pay
            month_pay[i] += pay
            if bal[i] <= 0 and closed_on_month[i] is None:
                closed_on_month[i] = month
        # 2) the pool CASCADES down the strategy order. Previously it went only to the focus loan and
        # any excess was silently destroyed — on a real multi-loan plan that is lakhs of the household's
        # own cash deleted from the model, making the payoff date months too pessimistic.
        pool = extra + freed + (spare if rolldown else 0.0)
        _pool_left, month_fees, from_pool = _cascade(pool, month)
        fees_paid += month_fees
        for i in range(n):
            month_pay[i] += from_pool[i]
        for i in range(n):
            if month_pay[i] > push[i]["total"] + 1e-6:
                push[i] = {"total": month_pay[i], "own_emi": min(emis[i], month_pay[i]),
                           "from_pool": max(0.0, month_pay[i] - emis[i])}
                peak_month[i] = month

    # Step-by-step plan, in the order loans actually clear. Every figure is what the simulation REALLY
    # paid (its own EMI + whatever the pool gave it in its biggest month) — not a reconstruction.
    # A loan that never clears inside the horizon is still listed, with closes_in_months = None.
    paid_order = sorted(range(n),
                        key=lambda i: (closed_on_month[i] if closed_on_month[i] is not None else 10**9, i))
    steps = []
    for i in paid_order:
        p = push[i]
        steps.append({
            "id": order[i]["id"], "name": order[i]["name"],
            "closes_in_months": closed_on_month[i],
            "monthly_payment": round(p["total"], 2),
            "own_emi": round(p["own_emi"], 2),
            "from_pool": round(p["from_pool"], 2),
            "extra": round(extra, 2),
            # The headline is only reachable from this month, once the loans below have closed and
            # handed over their EMIs. Presented without it, "pay Rs 2L a month" reads as money the
            # household does not have — when in fact it is money they are already paying elsewhere.
            "peak_from_month": peak_month[i],
            "funded_by_closing": [order[j]["name"] for j in range(n)
                                  if closed_on_month[j] is not None and peak_month[i] is not None
                                  and closed_on_month[j] <= peak_month[i] and j != i],
        })

    # complete = every loan actually cleared within the horizon. If False, `months` is the MAX_MONTHS
    # cap (a floor, not a real payoff) — the UI must not claim "debt-free in N months".
    complete = all(c is not None for c in closed_on_month)
    # A plan that asks for more in a month than EMIs-plus-surplus is not a plan, it is a wish. The
    # pool is only ever extra + freed EMIs + unused tails, so this cannot trip unless the cascade is
    # wrong — which is exactly why it is asserted rather than assumed.
    capacity = sum(l["emi_inr"] for l in active) + extra
    over = [order[i]["name"] for i in range(n) if push[i]["total"] > capacity + 1.0]
    return {
        "months": months,
        "monthly_capacity": round(capacity, 2),
        "exceeds_capacity": over,
        "complete": complete,
        "total_interest": round(total_interest, 2),
        # Kept apart from interest: a foreclosure fee is a cost of CHOOSING this plan, not the cost
        # of the borrowing. Folding it in would make one strategy look dearer to lend on than another.
        "prepayment_fees": round(fees_paid, 2),
        "total_cost": round(total_interest + fees_paid, 2),
        # `or 10**6` would read month 0 (a lump paid today) as "never reached" — hence the explicit
        # None test on every one of these sorts.
        "order": [{"id": order[i]["id"], "name": order[i]["name"],
                   "closes_in_months": closed_on_month[i]}
                  for i in sorted(range(n), key=lambda i: (
                      first_pool[i] is None,
                      first_pool[i] if first_pool[i] is not None else 10**6,
                      closed_on_month[i] if closed_on_month[i] is not None else 10**6))],
        # The loan spare cash actually lands on FIRST. Not the top of the rate sort, which a lender
        # refusing part payment can make unreachable for years.
        "attack_next": next((order[i]["name"] for i in sorted(
            range(n), key=lambda i: (first_pool[i] is None,
                                     first_pool[i] if first_pool[i] is not None else 10**6))
            if first_pool[i] is not None), None),
        "steps": steps,
        "never_amortises": never_amortises,
        # Per-loan detail a caller can diff between two runs of this same simulation.
        "by_loan": {order[i]["id"]: {
            "name": order[i]["name"], "loan_type": order[i].get("loan_type") or "standard",
            "rate_pct": order[i].get("rate_pct"), "emi_inr": order[i].get("emi_inr"),
            "balance_inr": round(start_bal[i], 2),
            "interest": round(int_by_loan[i], 2),
            "interest_cum": [round(x, 2) for x in int_cum_by_loan[i]],
            "closes_in_months": closed_on_month[i],
            "lump_applied": round(lump_used[i], 2),
            "prepayment_rule": prepay[i],
        } for i in range(n)},
        "interest_cum": [round(x, 2) for x in interest_cum],
        "lump": round(float(max(0.0, lump)), 2),
        "lump_unused": round(lump_left, 2),
        "lump_fees": round(lump_fees, 2),
    }


_AVALANCHE_SORT = next(sk for k, _l, _d, sk in _STRATEGIES if k == "avalanche")


def sim_ready(active: list[dict]) -> list[dict]:
    """Loan views the simulator can be trusted with.

    A GOLD loan is simulated on its PRINCIPAL, not on `balance_inr` (= principal + already-accrued
    interest). Feeding the combined figure in makes the sim charge interest on interest the lender
    never compounds, and can wrongly tip the loan into "never clears"."""
    return [dict(ln, balance_inr=ln["principal_outstanding"])
            if ln.get("loan_type") == "gold" and ln.get("principal_outstanding") is not None else ln
            for ln in active]


def payoff_simulation(active: list[dict], extra: float, lump: float = 0.0) -> dict:
    """The payoff plan exactly as the Debt tab runs it — avalanche order, the household's monthly
    surplus flowing — optionally with a one-off lump paid in today.

    Public on purpose: api/windfall needs the plan that is ALREADY running as its baseline, and a
    second implementation of this arithmetic would drift from the dates the Debt tab publishes.
    Run it twice, with and without the lump, and the difference is what the lump actually buys."""
    return _simulate(sim_ready(active), _AVALANCHE_SORT, extra, lump=lump)


def _strategies(active: list[dict], extra: float) -> dict:
    """Compare every strategy against a minimums-only baseline (no extra) so 'interest saved' is real."""
    if not active:
        return {"extra_per_month": extra, "baseline": None, "options": []}
    baseline = _simulate(active, lambda l: (-l["rate_pct"],), 0.0, rolldown=False)  # true minimums-only
    options = []
    for key, label, desc, sort_key in _STRATEGIES:
        sim = _simulate(active, sort_key, extra)
        # "Saved vs paying only the EMIs" is a REAL number only if that minimums-only baseline actually
        # finishes. When it doesn't (any loan whose EMI merely covers its interest never amortises), the
        # old code subtracted the MAX_MONTHS cap and presented the result as a financial fact — so
        # `months_saved` was literally "cap − months" and `interest_saved` moved if you edited the
        # constant. Report None instead and let the UI stay silent.
        options.append({
            "key": key, "label": label, "description": desc,
            "months": sim["months"], "complete": sim["complete"],
            "total_interest": sim["total_interest"],
            "prepayment_fees": sim.get("prepayment_fees", 0.0),
            "total_cost": sim.get("total_cost", sim["total_interest"]),
            "attack_next": sim.get("attack_next"),
            "monthly_capacity": sim.get("monthly_capacity"),
            "exceeds_capacity": sim.get("exceeds_capacity", []),
            "interest_saved": (round(baseline["total_interest"] - sim["total_interest"], 2)
                               if baseline["complete"] else None),
            "months_saved": (baseline["months"] - sim["months"]) if baseline["complete"] else None,
            "baseline_complete": baseline["complete"],
            "order": sim["order"],
            "steps": sim["steps"],
            "never_amortises": sim["never_amortises"],
        })
    # Pick one. Four options and no recommendation meant the screen asking "which loan to pay first"
    # had no answer to give. Cheapest total interest wins; ties break on the earlier debt-free date.
    # Only among options that actually clear — a plan that does not finish is not a recommendation.
    finishers = [o for o in options if o["complete"]]
    best = min(finishers, key=lambda o: (o["total_interest"], o["months"])) if finishers else None
    recommended = best["key"] if best else None
    why = None
    if best:
        rival = min((o for o in finishers if o["key"] != best["key"]),
                    key=lambda o: (o["total_interest"], o["months"]), default=None)
        first = (best["steps"] or [{}])[0].get("name")
        why = f"Attack {first} first" if first else best["label"]
        if rival:
            gap = round(rival["total_interest"] - best["total_interest"], 2)
            if gap > 0:
                why += f" — Rs {gap:,.0f} less interest than {rival['label'].lower()}"
    return {
        "extra_per_month": extra,
        "baseline": {"months": baseline["months"], "total_interest": baseline["total_interest"]},
        "options": options,
        "recommended": recommended,
        "recommended_why": why,
    }


def _pace_over_budget(net_spent: float, variable_planned: float,
                      days_elapsed: int, days_total: int) -> float:
    """How far AHEAD OF PACE this cycle's variable spend is, as of today. Positive = overspending.

    `net_spent` is cycle-to-DATE but `variable_planned` is a WHOLE-cycle target, so comparing them raw
    made every early-cycle day look like a massive underspend (on day 2 you've "spent nothing", so the
    'safe to prepay this cycle' figure was the full surplus) and every late day look like an overspend.
    Pro-rating the target by the share of the cycle actually elapsed compares like with like.
    """
    if days_total <= 0:                       # no cycle info → fall back to the raw comparison
        return round(net_spent - variable_planned, 2)
    elapsed = max(0, min(days_elapsed, days_total))
    return round(net_spent - variable_planned * elapsed / days_total, 2)


def _owned_loan(db: Session, loan_id: int, household: Household) -> Loan:
    loan = db.query(Loan).filter(Loan.id == loan_id,
                                 Loan.household_id == household.id).first()
    if not loan:
        raise HTTPException(404, "Loan not found")
    return loan


# ── Endpoints ────────────────────────────────────────────────
def compute_debt(db: Session, household_id: int | None = None) -> dict:
    today = date.today()
    q = db.query(Loan)
    if household_id is not None:
        q = q.filter(Loan.household_id == household_id)
    rows = q.all()
    loans = [_view(l, today) for l in rows]
    # Attach the AFTER-TAX cost of each loan. The headline rate is not what a loan costs when its
    # interest is deductible, and ranking on the headline puts the cheapest debt first — an education
    # loan at 9.5% under 80E is cheaper than a gold loan at 9.1% with no relief. On the new regime
    # nothing is deductible and effective collapses back to nominal, which is the safe default.
    try:
        from services.tax import effective_rates
        from services.tax_context import household_tax_context

        regime, income_by_holder, let_out = household_tax_context(db, household_id)
        by_name = {t.name: t for t in effective_rates(loans, income_by_holder, regime,
                                                      let_out_holders=let_out)}
        for l in loans:
            t = by_name.get(l["name"])
            if t is not None:
                l["effective_rate_pct"] = t.effective_rate_pct
                l["tax_section"] = t.section
                l["tax_saves_pct"] = t.saves_pct
                l["tax_why"] = t.why
                l["tax_regime"] = regime
    except Exception:  # noqa: BLE001 — a tax-model failure must never take down the debt page
        logger.exception("effective-rate annotation failed; falling back to nominal rates")
        for l in loans:
            l.setdefault("effective_rate_pct", l.get("rate_pct"))

    open_loans = [l for l in loans if not l["closed"] and l["balance_inr"] > 0]
    # SERVICED = loans the household actually owes + pays → these drive the headline totals (your debt,
    # your EMI, your interest). A gold loan in a parent's name that YOU service counts here — it's on you.
    serviced = [l for l in open_loans if l.get("serviced_by_household", True)]
    # EXTERNAL = someone else pays it (a parent's own loan) → shown + tagged, but out of your totals + plan.
    # ATTACKABLE = every serviced loan is a first-class payoff-plan step, INCLUDING a gold loan: paid at
    # its expected ₹/mo it amortises, so the (generic) simulator clears it like any other loan. Only a
    # truly external loan (someone else pays it) is left out of the plan.
    #
    # ...EXCEPT a loan whose beneficiary is external. You service it, so its EMI is your cash-flow and
    # its balance is your liability, but PREPAYING it hands your money to someone else's principal.
    # It stays in every total and drops out of the attack order.
    active = [l for l in serviced if (l.get("beneficiary") or "household") != "external"]
    active.sort(key=lambda l: l["rate_pct"], reverse=True)  # avalanche order
    closed = [l for l in loans if l["closed"] or l["balance_inr"] <= 0]
    other_open = [l for l in open_loans if l not in active]   # = external (not serviced)

    total_balance = round(sum(l["balance_inr"] for l in serviced), 2)
    total_emi = round(sum(l["emi_inr"] for l in serviced), 2)
    monthly_interest = round(sum(l["balance_inr"] * l["rate_pct"] / 1200.0 for l in serviced), 2)

    # REAL spare cash to attack debt — not a hardcoded number.
    #  • surplus         = the monthly amount you COULD redirect to debt if you hold budget
    #                      (free-after-fixed − planned variable budget). Drives the projected plan.
    #  • actionable      = what's safe to prepay THIS cycle. If you're over budget, that surplus is
    #                      already spent, so actionable = surplus − overspend, floored at 0. Drives the
    #                      "this month" action so we never tell you to prepay money you don't have.
    #  • data_ok         = False if budget/cashflow couldn't be computed → UI shows "plan unavailable",
    #                      NOT a misleading "₹0 spare".
    surplus = free_after_fixed = variable_planned = over_budget = net_spent = 0.0
    data_ok = True
    try:
        from api.budget import compute_budget
        from api.cashflow import compute_cashflow
        cash = compute_cashflow(db, household_id)
        bud = compute_budget(month=f"{today.year:04d}-{today.month:02d}", mode="cycle",
                             db=db, household_id=household_id)
        free_after_fixed = round(max(0.0, cash.get("leftover", 0) or 0.0), 2)
        variable_planned = round(sum(i["limit_inr"] for i in bud.get("items", []) if i["limit_inr"] > 0), 2)
        surplus = round(max(0.0, free_after_fixed - variable_planned), 2)
        net_spent = round(bud.get("net_spent_inr", 0) or 0.0, 2)   # cycle-to-DATE, not a full month
        cyc = bud.get("cycle") or {}
        over_budget = _pace_over_budget(net_spent, variable_planned,
                                        cyc.get("days_elapsed") or 0, cyc.get("days_total") or 0)
    except Exception:  # noqa: BLE001 — never fake a ₹0 surplus; flag it and LOG why
        logger.exception("compute_debt: cashflow/budget unavailable — plan reported with data_ok=False")
        data_ok = False
    actionable = round(max(0.0, surplus - max(0.0, over_budget)), 2)

    # Compare strategies at the SURPLUS you COULD direct to debt — this is the only amount at which the
    # methods actually differ (at ₹0 extra every strategy collapses to plain amortization and the choice
    # would do nothing, which is why the picker felt broken). Each option's `order` is the true attack
    # priority (avalanche = highest rate first) — the UI drives the "attack next" badge off THAT, not the
    # close-order. `current` = the same plan at today's actual actionable, for an honest contrast.
    plan_extra = surplus if surplus > 0 else actionable
    sim_input = sim_ready(active)
    strategies = _strategies(sim_input, plan_extra)
    strategies["plan_extra"] = plan_extra
    if actionable < surplus:
        cur = {o["key"]: o for o in _strategies(sim_input, actionable)["options"]}
        for o in strategies["options"]:
            c = cur.get(o["key"])
            if c:
                o["current"] = {"extra": actionable, "months": c["months"],
                                "complete": c["complete"], "total_interest": c["total_interest"]}
    # The REAL cash shortfall = what you spent beyond your actual free cash (not merely over an arbitrary
    # budget target). Uses the REAL cycle-to-date net_spent from the budget engine — it used to be
    # reconstructed as variable_planned + over_budget, which silently breaks once over_budget is
    # pace-relative rather than a raw whole-cycle difference.
    overspend = round(max(0.0, net_spent - free_after_fixed), 2)
    strategies["surplus_context"] = {
        "free_after_fixed": free_after_fixed, "variable_planned": variable_planned,
        "surplus": surplus, "over_budget": over_budget, "actionable": actionable, "data_ok": data_ok,
        "overspend": overspend,
    }
    # Only when you've genuinely overspent your available cash: how to cover THAT gap without a new loan.
    if overspend > 0:
        from services.recovery import recovery_plan
        strategies["recovery"] = recovery_plan(db, household_id, overspend)

    # The ONE money move this cycle — chosen deterministically (engine owns the numbers); the AI sentence
    # is fetched separately via /debt/move-narration so this endpoint never waits on the model.
    from services import money_moves
    ava = next((o for o in strategies["options"] if o["key"] == "avalanche"), None)
    first_step = ava["steps"][0] if ava and ava.get("steps") else None
    from services.money_format import household_symbol
    strategies["move"] = money_moves.decide(strategies["surplus_context"], strategies.get("recovery"),
                                            first_step, household_symbol(db, household_id))

    # Every date in the plan assumes the extra actually goes in each month. Measure whether it has,
    # and when it has not, publish the SAME plan re-run at the observed rate — so a household that is
    # not following the plan sees the dates that follow from what it is really doing, beside the ones
    # that follow from the plan. Without this the screen keeps showing a date that stopped being true.
    try:
        from services.adherence import measure
        adh = measure(db, household_id, plan_extra)
        strategies["adherence"] = adh
        # `too_early` means there is no observed monthly rate yet — re-running the plan at an average
        # of zero would publish dates that contradict the payment the household just made.
        if adh["actual_avg"] is not None and adh["status"] not in ("on_plan", "too_early"):
            strategies["at_actual"] = {
                o["key"]: {
                    "extra": adh["actual_avg"],
                    **{k: v for k, v in _simulate(sim_input, sk, adh["actual_avg"]).items()
                       if k in ("months", "complete", "total_interest", "steps")},
                }
                for (o, (_k, _l, _d, sk)) in zip(strategies["options"], _STRATEGIES, strict=False)
            }
    except Exception:  # noqa: BLE001 — a measurement failure must not take down the debt page
        logger.exception("adherence measurement failed")
        strategies["adherence"] = None
    return {
        "loans": active + other_open + closed,
        "total_balance": total_balance,
        "total_emi": total_emi,
        "monthly_interest": monthly_interest,
        "strategies": strategies,
    }


@router.get("/debt")
def get_debt(db: Session = Depends(get_db),
             household: Household = Depends(get_current_household)):
    return compute_debt(db, household.id)


@router.get("/debt/move-narration")
def get_move_narration(db: Session = Depends(get_db),
                       household: Household = Depends(get_current_household)):
    """The AI-narrated one-liner for this cycle's money move. Separate from /debt so the page renders
    the deterministic move instantly and only this call waits on the model (with a template fallback)."""
    from services import money_moves
    move = compute_debt(db, household.id)["strategies"].get("move")
    if not move:
        return {"headline": None, "ai": False}
    narrated = money_moves.narrate(move)
    return {"headline": narrated, "ai": narrated != move.get("headline")}


@router.get("/debt/{loan_id}")
def get_loan_detail(loan_id: int, db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    loan = _owned_loan(db, loan_id, household)
    today = date.today()
    view = _view(loan, today)
    # A gold loan is a bullet account with a flexible ledger — it doesn't amortise, so there's no fixed
    # EMI schedule to show (the payment history lives in view["payments"] instead).
    view["schedule"] = ([] if _is_gold(loan) else
                        loan_math.amortization_schedule(loan.original_principal, loan.annual_rate_pct,
                                                        loan.tenure_months))
    # The schedule above runs from origination, which answers a question about the past. This one runs
    # from TODAY'S balance so you can pick the month to foreclose in and see what it will cost that
    # month. Gold loans get it too — they amortise fine against a fixed expected payment.
    payment = (loan.expected_monthly_payment if _is_gold(loan) else None) or view.get("emi_inr") or 0.0
    # A gold loan's balance_inr includes accrued-but-unpaid interest; the schedule projects the
    # PRINCIPAL forward, since that is what a payment amortises and what closing the account costs.
    balance = (view.get("principal_outstanding") if _is_gold(loan) else None) or view.get("balance_inr") or 0.0

    # Payment steps, derived from the loan's own state rather than its label. A loan still in
    # moratorium pays interest only until the moratorium ends, then the full EMI — expressed as a
    # step so the schedule needs no knowledge of loan types. Everything else is one flat step.
    steps: list[tuple[int, float]] = [(1, payment)]
    post_emi = view.get("post_moratorium_emi")
    if view.get("phase") == "moratorium" and post_emi:
        elapsed = loan_math.months_between(loan.disbursement_date, today)
        months_to_go = max(1, (view.get("moratorium_months") or 0) - elapsed)
        steps.append((months_to_go + 1, float(post_emi)))

    # Run to whatever this loan actually needs rather than a constant, so the schedule length and
    # months_remaining cannot disagree. Capped so a never-clearing loan still terminates.
    needed = view.get("months_remaining") or 0
    view["forward_schedule"] = loan_math.forward_schedule(
        current_balance=balance,
        annual_rate_pct=loan.annual_rate_pct,
        monthly_payment=payment,
        start=today,
        max_rows=min(600, max(360, int(needed) + 2)),
        steps=steps,
    )
    return view


# Flat API fields that get folded into the flexi_terms JSON blob (not columns).
_FLEXI_FLAT = ("disbursed_amount", "disbursements", "moratorium_months", "moratorium_interest_mode")


def _column_fields(body: LoanIn) -> dict:
    data = body.model_dump()
    for k in _FLEXI_FLAT:
        data.pop(k, None)
    return data


@router.post("/debt")
def create_loan(body: LoanIn, db: Session = Depends(get_db),
                household: Household = Depends(get_current_household)):
    loan = Loan(household_id=household.id, **_column_fields(body), flexi_terms=_pack_flexi(body))
    db.add(loan)
    db.commit()
    db.refresh(loan)
    return _view(loan, date.today())


@router.put("/debt/{loan_id}")
def update_loan(loan_id: int, body: LoanIn, db: Session = Depends(get_db),
                household: Household = Depends(get_current_household)):
    loan = _owned_loan(db, loan_id, household)
    for k, v in _column_fields(body).items():
        setattr(loan, k, v)
    loan.flexi_terms = _pack_flexi(body)
    db.commit()
    db.refresh(loan)
    return _view(loan, date.today())


@router.post("/debt/{loan_id}/payments")
def add_payment(loan_id: int, body: PaymentIn, db: Session = Depends(get_db),
                household: Household = Depends(get_current_household)):
    """Log a payment against ANY loan (any amount, any date, several a month, or none).

    On a gold loan the ledger IS the balance. On a standard or flexi loan the EMI is already assumed
    paid by the amortisation, so a row here is an EXTRA payment and comes straight off principal.
    Either way the balance and every payoff date re-derive from it on the next read."""
    loan = _owned_loan(db, loan_id, household)
    if body.amount <= 0:
        raise HTTPException(400, "Payment amount must be positive")
    if body.kind not in ("extra", "skipped_emi"):
        raise HTTPException(400, "kind must be 'extra' or 'skipped_emi'")
    # Where the money came from. Default "bank" keeps existing behaviour: a payment made without
    # saying is NOT silently taken out of the wallet, because a balance that moves on its own stops
    # being something you can reconcile.
    src = (getattr(body, "funded_from", None) or "bank").lower()
    if src not in ("wallet", "bank", "other"):
        raise HTTPException(400, "funded_from must be wallet, bank or other")
    if src == "wallet":
        from services import wallet
        try:
            wallet.spend(db, household.id, body.amount, on=body.date.isoformat(),
                         loan_id=loan.id, note=body.note or "loan payment")
        except ValueError as e:
            # Refusing is the point: an overdrawn wallet is another figure to reconcile, which is
            # what this feature exists to remove.
            raise HTTPException(400, str(e)) from e

    db.add(LoanPayment(loan_id=loan.id, household_id=household.id, kind=body.kind,
                       date=body.date, amount=body.amount, note=body.note))
    db.commit()
    db.refresh(loan)
    out = _view(loan, date.today())
    if src == "wallet":
        from services import wallet
        out["wallet_balance"] = wallet.balance(db, household.id)
    return out


@router.post("/debt/{loan_id}/skip")
def skip_emi(loan_id: int, body: SkipIn, db: Session = Depends(get_db),
             household: Household = Depends(get_current_household)):
    """Record an EMI that was NOT paid.

    Without this the plan silently assumes every scheduled EMI landed, so a missed month makes the
    app show a lower balance and an earlier payoff date than reality. Defaults to the loan's own
    current EMI, which is the amount that did not leave the account."""
    loan = _owned_loan(db, loan_id, household)
    amount = body.amount if body.amount is not None else _emi_of(loan, date.today())
    if amount is None or amount <= 0:
        raise HTTPException(400, "Nothing to skip: this loan has no monthly payment to miss")
    db.add(LoanPayment(loan_id=loan.id, household_id=household.id, kind="skipped_emi",
                       date=body.date, amount=amount, note=body.note or "EMI skipped"))
    db.commit()
    db.refresh(loan)
    return _view(loan, date.today())


@router.delete("/debt/{loan_id}/payments/{payment_id}")
def delete_payment(loan_id: int, payment_id: int, db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)):
    """Remove a mistaken payment; the balance re-derives from the remaining ledger."""
    loan = _owned_loan(db, loan_id, household)
    p = (db.query(LoanPayment)
         .filter(LoanPayment.id == payment_id, LoanPayment.loan_id == loan.id,
                 LoanPayment.household_id == household.id).first())
    if not p:
        raise HTTPException(404, "Payment not found")
    db.delete(p)
    db.commit()
    db.refresh(loan)
    return _view(loan, date.today())


@router.post("/debt/{loan_id}/preclose")
def preclose_loan(loan_id: int, body: PrecloseIn, db: Session = Depends(get_db),
                  household: Household = Depends(get_current_household)):
    loan = _owned_loan(db, loan_id, household)
    if body.mode == "foreclose":
        loan.closed = True
        loan.closed_on = date.today()
    elif body.mode == "prepay":
        if not body.amount or body.amount <= 0:
            raise HTTPException(400, "Prepay needs a positive amount")
        loan.extra_principal = (loan.extra_principal or 0.0) + body.amount
        # if the prepayment clears it, mark closed — via the shared flexi-aware state (#8), so a
        # flexi/moratorium loan isn't mis-evaluated by the plain amortization model after a prepayment.
        st, _ = _state_of(loan, date.today())
        if st.current_balance <= 0:
            loan.closed = True
            loan.closed_on = date.today()
    else:
        raise HTTPException(400, "mode must be 'foreclose' or 'prepay'")
    db.commit()
    db.refresh(loan)
    return _view(loan, date.today())


@router.post("/debt/{loan_id}/disburse")
def add_disbursement(loan_id: int, body: DisburseIn, db: Session = Depends(get_db),
                     household: Household = Depends(get_current_household)):
    """Record a new disbursement tranche on a flexi loan. Interest re-computes on
    the new total drawn."""
    loan = _owned_loan(db, loan_id, household)
    if not _is_flexi(loan):
        raise HTTPException(400, "Disbursements only apply to flexi loans")
    if body.amount <= 0:
        raise HTTPException(400, "Disbursement amount must be positive")
    ft = dict(_ft(loan))
    tranches = list(ft.get("disbursements") or [])
    tranches.append({"amount": body.amount, "date": body.date.isoformat(), "note": body.note or ""})
    tranches.sort(key=lambda t: t.get("date", ""))
    ft["disbursements"] = tranches
    loan.flexi_terms = ft
    db.commit()
    db.refresh(loan)
    return _view(loan, date.today())


@router.delete("/debt/{loan_id}/disburse/{index}")
def delete_disbursement(loan_id: int, index: int, db: Session = Depends(get_db),
                        household: Household = Depends(get_current_household)):
    loan = _owned_loan(db, loan_id, household)
    ft = dict(_ft(loan))
    tranches = list(ft.get("disbursements") or [])
    if index < 0 or index >= len(tranches):
        raise HTTPException(404, "Disbursement not found")
    tranches.pop(index)
    ft["disbursements"] = tranches
    loan.flexi_terms = ft
    db.commit()
    db.refresh(loan)
    return _view(loan, date.today())


@router.delete("/debt/{loan_id}")
def delete_loan(loan_id: int, db: Session = Depends(get_db),
                household: Household = Depends(get_current_household)):
    loan = _owned_loan(db, loan_id, household)
    db.delete(loan)
    db.commit()
    return {"deleted": loan_id}
