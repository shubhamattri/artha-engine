""""Can I afford it?" — the forward-looking check (North Star #2).

Two regimes:
  • Everyday spend → checks BOTH the overall free budget AND (if chosen) the category budget; the
    MORE RESTRICTIVE one binds. Overall exhausted ⇒ HOLD even if the category still has room.
  • Big purchase (a house, a car) → a different question entirely: down-payment vs net worth, the
    new EMI vs income (FOIR) on top of existing loans. Triggers automatically for large amounts.
"""

from __future__ import annotations


from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_household
from config import settings
from database import get_db
from models.tenant import Household
from services.loan_math import emi as _loan_emi

router = APIRouter()

_VARIABLE = ["SPEND", "FEE"]


class AffordIn(BaseModel):
    amount: float
    category: str | None = None


def _llm_reason(prompt: str, fallback: str) -> tuple[str, bool]:
    from services import llm
    if not llm.available():
        return fallback, False
    out = llm.complete(prompt, max_tokens=320,
                       system="You are Artha, a trustworthy household-finance advisor. Concrete and kind; "
                              "use ONLY the numbers given, never invent figures. No preamble.")
    return (out or fallback), bool(out)


# These were literals here until they had to be changed for a different rate environment. They now
# live in config (see the "Planning assumptions" block) so no engine restates a market guess.
_CONFIG_ALIASES = {"HOME_LOAN_RATE": "home_loan_rate", "HOME_LOAN_YEARS": "home_loan_years",
                   "EQUITY_RETURN": "equity_return", "LTV": "home_ltv", "FOIR_CAP": "foir_cap"}


def __getattr__(name: str):
    # Resolved on every access, so an env override lands without a reimport.
    if name in _CONFIG_ALIASES:
        return getattr(settings, _CONFIG_ALIASES[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _emi_factor(annual_rate: float, years: int) -> float:
    """Monthly EMI per ₹1 of loan — one amortisation implementation (loan_math), never a second copy."""
    return _loan_emi(1.0, annual_rate * 100.0, years * 12)


def _big_ticket(body: AffordIn, db: Session, cash: dict, household_id: int | None = None) -> dict:
    """Strategic major-purchase engine (treat `amount` as the PURCHASE PRICE).

    Uses the bank/planner stack: FOIR + LTV for loan eligibility, a liquidity waterfall
    (cash→gold→equity, keeping an emergency buffer) for the down payment, and a prepay-vs-invest
    arbitrage to recommend how much to actually put down. Tells you WHERE the money comes from."""
    from api.debt import compute_debt
    from api.networth import compute_assets

    price = body.amount
    income = cash.get("income", 0) or 1
    existing_emi = (compute_debt(db, household_id).get("total_emi", 0) or 0)
    by_type = compute_assets(db, household_id).get("by_type", {})
    liq_cash = by_type.get("cash", 0.0)
    gold = by_type.get("gold", 0.0)
    equity = by_type.get("stock", 0.0)

    # 1) Loan eligibility = min(LTV cap, FOIR-based cap)
    rate, foir_cap = settings.home_loan_rate, settings.foir_cap
    factor = _emi_factor(rate, settings.home_loan_years)
    max_emi = max(0.0, foir_cap * income - existing_emi)
    loan_by_foir = max_emi / factor
    loan_by_ltv = settings.home_ltv * price
    max_loan = round(min(loan_by_foir, loan_by_ltv))

    # 2) Down payment required = price − eligible loan
    down_needed = round(max(price - max_loan, 0))

    # 3) Liquidity waterfall — keep ~6 months of living costs as an emergency buffer
    emergency = round(6 * (cash.get("fixed", 0) + cash.get("leftover", 0)))
    usable_cash = max(0.0, liq_cash - emergency)
    funding, need = [], down_needed
    for label, pool in (("Cash & savings", usable_cash), ("Gold", gold), ("Equity / MF", equity)):
        use = round(min(need, pool))
        if use > 0:
            funding.append({"source": label, "amount": use})
            need -= use
    fundable = need <= max(1, 0.01 * down_needed)
    shortfall = round(max(need, 0))

    new_emi = round(max_loan * factor)
    foir = (existing_emi + new_emi) / income
    emi_ok = foir <= foir_cap

    verdict = "go" if (fundable and emi_ok) else ("tight" if (fundable or emi_ok) else "hold")
    equity_return = settings.equity_return
    keep_invested = equity_return > rate   # borrow to the max, keep money invested

    reason, ai = _llm_reason(
        f"Household buying a ₹{price:,.0f} home. Income ₹{income:,.0f}/mo; existing EMIs ₹{existing_emi:,.0f}. "
        f"Liquid assets: cash ₹{liq_cash:,.0f}, gold ₹{gold:,.0f}, equity ₹{equity:,.0f}; keep ₹{emergency:,.0f} "
        f"emergency buffer. Max eligible loan ₹{max_loan:,.0f} (min of {settings.home_ltv*100:.0f}% LTV and "
        f"{foir_cap*100:.0f}% FOIR); so down payment "
        f"needed ₹{down_needed:,.0f}, funded as {funding} (shortfall ₹{shortfall:,.0f}). New EMI ₹{new_emi:,.0f} → "
        f"total EMIs {foir*100:.0f}% of income. Home-loan {rate*100:.1f}% vs ~{equity_return*100:.0f}% "
        f"equity return, so {'take the max loan and keep cash invested' if keep_invested else 'pay down more'}. "
        f"Verdict {verdict.upper()}. Write 3 short sentences: bottom line; exactly where the down payment comes "
        "from; and the one strategic move (down-payment size vs keeping invested).",
        fallback=(f"Eligible loan ₹{max_loan:,.0f}; put down ₹{down_needed:,.0f} (from "
                  + ", ".join(f"₹{f['amount']:,.0f} {f['source'].lower()}" for f in funding) + "); "
                  f"EMI ₹{new_emi:,.0f} → {foir*100:.0f}% of income."
                  + (f" Short ₹{shortfall:,.0f}." if shortfall else "")))
    return {
        "verdict": verdict, "amount": price, "big_ticket": True,
        "price": price, "max_loan": max_loan, "down_payment": down_needed,
        "funding": funding, "shortfall": shortfall, "fundable": fundable,
        "emergency_buffer": emergency, "new_emi": new_emi, "existing_emi": round(existing_emi),
        "foir_pct": round(foir * 100), "emi_ok": emi_ok,
        "keep_invested": keep_invested, "loan_rate_pct": round(rate * 100, 1),
        "reason": reason, "ai": ai,
    }


@router.post("/afford")
def afford(body: AffordIn, db: Session = Depends(get_db),
           household: Household = Depends(get_current_household)):
    from datetime import date as _date

    from api.budget import compute_budget
    from api.cashflow import compute_cashflow
    from services.cycle import card_cycle_spend

    cash = compute_cashflow(db, household.id)
    income = cash.get("income", 0) or 1
    monthly_free = cash["leftover"]

    # A purchase far bigger than a month's free budget is a capital decision, not a spend decision.
    if body.amount >= max(200000, 3 * income):
        return _big_ticket(body, db, cash, household.id)

    spent = card_cycle_spend(db, _date.today(), txn_types=_VARIABLE, household_id=household.id)
    overall_after = round(monthly_free - spent - body.amount, 2)

    # category constraint (when a budgeted category is chosen)
    cat_after = None
    cat_target = cat_spent = None
    if body.category:
        plan = compute_budget(mode="cycle", db=db, household_id=household.id)
        item = next((i for i in plan["items"] if i["category"] == body.category), None)
        if item and item["limit_inr"] > 0:
            cat_target, cat_spent = item["limit_inr"], item["spent_inr"]
            cat_after = round(cat_target - cat_spent - body.amount, 2)

    # the BINDING constraint is the more restrictive of overall and category
    binding_after = overall_after if cat_after is None else min(overall_after, cat_after)
    binds_on_category = cat_after is not None and cat_after < overall_after
    tight_band = 0.15 * max(monthly_free, 1)

    if binding_after < 0:
        verdict = "hold"
        where = f"your {body.category} budget" if binds_on_category else "your free budget this cycle"
        reason = f"This pushes you ₹{abs(binding_after):,.0f} past {where}."
    elif binding_after < tight_band:
        verdict = "tight"
        reason = f"You can, but it leaves only ₹{binding_after:,.0f} in {'that category' if binds_on_category else 'the cycle'}."
    else:
        verdict = "go"
        reason = f"Go ahead — ₹{binding_after:,.0f} still free after it."

    ctx = (f"Spend ₹{body.amount:,.0f}{' on ' + body.category if body.category else ''}. "
           f"Overall free ₹{monthly_free:,.0f}, already spent ₹{spent:,.0f} → ₹{overall_after:,.0f} left after.")
    if cat_after is not None:
        ctx += f" The {body.category} budget ₹{cat_target:,.0f}, spent ₹{cat_spent:,.0f} → ₹{cat_after:,.0f} left after."
    reason, ai = _llm_reason(
        ctx + f" Verdict {verdict.upper()} (the {'category' if binds_on_category else 'overall'} budget binds). "
        "One short, specific sentence advising them.",
        fallback=reason)

    return {
        "verdict": verdict, "amount": body.amount, "big_ticket": False,
        "monthly_free": round(monthly_free, 2), "spent_so_far": round(spent, 2),
        "overall_after": overall_after, "category_after": cat_after,
        "binds_on_category": binds_on_category, "remaining_after": binding_after,
        "reason": reason, "ai": ai,
    }
