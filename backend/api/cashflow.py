"""Monthly cash-flow engine — income, fixed expenses, savings (all editable).

The recurring two-ledger model (Cowork reconciliation):
    income − EMIs(from loans) − fixed − savings − sinking − insurance = leftover
EMIs are derived from the loans table, never duplicated here.

`leftover` is the money AVAILABLE for variable spend, not what survives it. That is deliberate and
load-bearing: the cockpit income breakdown and MonthlyFlow both render it as the final row of a
waterfall, so it has to be the residual or the rows stop summing to income.

What is genuinely spare is `spare` (= leftover − spent). Both are published because they answer
different questions: leftover sizes the month's budget, spare is what a payoff or investment plan
may commit. Do not conflate them — briefly redefining leftover as the spare figure double-subtracted
in cockpit's safe_to_spend and put −22,500 on the main screen.

`spent` comes from api/budget, which is the only module that filters correctly. The raw transaction
table cannot be summed for spend: across 15 accounts a card purchase and the bank debit settling it
both appear, alongside inter-account transfers, and the raw total runs several times income.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_household
from database import get_db
from models.household import CashflowLine, Loan
from models.tenant import Household

router = APIRouter()

logger = logging.getLogger(__name__)

KINDS = {"income", "fixed", "saving"}


class LineIn(BaseModel):
    kind: str
    label: str
    amount_inr: float
    holder: str | None = None
    active: bool = True
    start_date: date | None = None   # when this line began (None = always-on)
    end_date: date | None = None


def _emi_total(db: Session, household_id: int | None = None) -> float:
    # Use the ONE shared loan-obligation service so a flexi/moratorium loan (ICICI Education) costs the
    # SAME EMI here as it does on the Debt tab — no more divergent per-tab formulas.
    from services import loan_obligation
    today = date.today()
    total = 0.0
    # SERVICED loans (the household actually pays them) are its real cash-flow — INCLUDING a gold loan in a
    # parent's name that's off FOIR but paid by the household. A truly external loan (a parent pays it) has
    # serviced_by_household=False → out of cash-flow. This is the axis that matters for leftover, not FOIR.
    q = db.query(Loan).filter(Loan.closed.is_(False), Loan.serviced_by_household.is_(True))
    if household_id is not None:
        q = q.filter(Loan.household_id == household_id)
    for loan in q.all():
        st, _ = loan_obligation.state_of(loan, today)
        if st.current_balance > 0:
            total += loan_obligation.monthly_emi(loan, today)
    return total


def _line(l: CashflowLine) -> dict:
    return {
        "id": l.id, "kind": l.kind, "label": l.label,
        "amount_inr": l.amount_inr, "holder": l.holder, "active": l.active,
        "start_date": l.start_date.isoformat() if l.start_date else None,
        "end_date": l.end_date.isoformat() if l.end_date else None,
    }


@router.get("/cashflow")
def list_cashflow(db: Session = Depends(get_db),
                  household: Household = Depends(get_current_household)):
    lines = db.query(CashflowLine).filter(CashflowLine.household_id == household.id).all()
    by_kind = {k: [_line(l) for l in lines if l.kind == k] for k in KINDS}
    return by_kind


def compute_cashflow(db: Session, household_id: int | None = None) -> dict:
    q = db.query(CashflowLine).filter(CashflowLine.active.is_(True))
    if household_id is not None:
        q = q.filter(CashflowLine.household_id == household_id)
    lines = q.all()
    income = sum(l.amount_inr for l in lines if l.kind == "income")
    fixed = sum(l.amount_inr for l in lines if l.kind == "fixed")
    unallocated = sum(l.amount_inr for l in lines if l.kind == "saving")
    # Investment contributions that actually debit the bank each month (SIP/RD/PPF,
    # not EPF-at-source) — single source of truth from the recurring engine.
    from api.recurring import compute_recurring  # local import avoids a cycle
    from api.sinking import compute_sinking
    from api.insurance import compute_insurance
    invested = compute_recurring(db, household_id)["takehome_outflow"]
    sinking = compute_sinking(db, household_id)["monthly_set_aside"]   # ₹/mo into sinking buckets
    insurance = round(compute_insurance(db, household_id)["annual_premium"] / 12.0, 2)   # protection premiums /mo
    saving = unallocated + invested
    emi = _emi_total(db, household_id)
    # AVAILABLE to spend. Consumers render this as the closing row of an income waterfall, so it
    # must stay the residual — see the module docstring.
    leftover = income - emi - fixed - saving - sinking - insurance

    # What actually left the account this cycle. From api/budget, the only module that excludes
    # transfers, card settlements and EMI debits.
    spent = 0.0
    try:
        from api.budget import compute_budget
        spent = float(compute_budget(db=db, household_id=household_id).get("net_spent_inr") or 0.0)
    except Exception:  # noqa: BLE001 — a budget failure must not take down the cash-flow page
        logger.exception("could not read actual spend; spare degrades to leftover")

    spare = leftover - spent      # what a payoff or investment plan may actually commit
    return {
        "income": round(income, 2),
        "emi": round(emi, 2),
        "fixed": round(fixed, 2),
        "saving": round(saving, 2),
        "invested": round(invested, 2),       # into tracked instruments
        "unallocated": round(unallocated, 2),  # set aside but not in an instrument
        "sinking": round(sinking, 2),         # monthly set-aside into lumpy-expense buckets
        "insurance": round(insurance, 2),     # life/health premiums (from insurance policies)
        "leftover": round(leftover, 2),       # AVAILABLE for variable spend (the waterfall residual)
        "spent": round(spent, 2),             # actually spent this cycle, net of refunds
        "spare": round(spare, 2),             # leftover − spent: what a plan may commit
        "lines": {
            "income": [_line(l) for l in lines if l.kind == "income"],
            "fixed": [_line(l) for l in lines if l.kind == "fixed"],
            "saving": [_line(l) for l in lines if l.kind == "saving"],
        },
    }


@router.get("/cashflow/summary")
def cashflow_summary(db: Session = Depends(get_db),
                     household: Household = Depends(get_current_household)):
    return compute_cashflow(db, household.id)


def _owned_cashline(db: Session, line_id: int, household: Household) -> CashflowLine:
    line = db.query(CashflowLine).filter(CashflowLine.id == line_id,
                                         CashflowLine.household_id == household.id).first()
    if not line:
        raise HTTPException(404, "Line not found")
    return line


@router.post("/cashflow")
def create_line(body: LineIn, db: Session = Depends(get_db),
                household: Household = Depends(get_current_household)):
    if body.kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(KINDS)}")
    line = CashflowLine(household_id=household.id, **body.model_dump())
    db.add(line)
    db.commit()
    db.refresh(line)
    return _line(line)


@router.put("/cashflow/{line_id}")
def update_line(line_id: int, body: LineIn, db: Session = Depends(get_db),
                household: Household = Depends(get_current_household)):
    line = _owned_cashline(db, line_id, household)
    for k, v in body.model_dump().items():
        setattr(line, k, v)
    db.commit()
    db.refresh(line)
    return _line(line)


@router.delete("/cashflow/{line_id}")
def delete_line(line_id: int, db: Session = Depends(get_db),
                household: Household = Depends(get_current_household)):
    line = _owned_cashline(db, line_id, household)
    db.delete(line)
    db.commit()
    return {"deleted": line_id}
