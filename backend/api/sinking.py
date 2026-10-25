"""Sinking funds — set aside ₹/month for lumpy costs, draw down when spent.

  GET    /sinking               list + computed balances + totals
  POST   /sinking               add a fund
  PUT    /sinking/{id}          edit
  POST   /sinking/{id}/withdraw  log a spend from the fund (e.g. booked tickets)
  DELETE /sinking/{id}
"""

from __future__ import annotations

from datetime import date

from api.deps import get_current_household
from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from models.sinking import SinkingFund
from models.tenant import Household
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter()


class SinkingIn(BaseModel):
    name: str
    monthly_contribution: float = 0.0
    opening_balance: float = 0.0
    as_of_date: date
    target_annual: float | None = None
    holder: str | None = "Household"
    active: bool = True
    notes: str | None = None


class WithdrawIn(BaseModel):
    amount: float
    date: date
    note: str | None = None


def _months(start: date, end: date) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def _view(f: SinkingFund, today: date) -> dict:
    """One fund's figures.

    `balance` is what the fund SHOULD hold if every monthly set-aside was actually transferred. It is
    an intention, not an observation: nothing here reconciles those transfers against real debits the
    way services/commitments does for every other recurring outflow, and a fund whose transfer was
    never made looks identical to one that was funded every month.

    That distinction is published rather than buried, because services/recovery offers this balance as
    a way to cover an overspend. Telling a household already over budget to draw money that was never
    set aside is the worst direction for this to be wrong in, so `assumed_inr` states how much of the
    balance has never been observed and consumers can qualify it.
    """
    months = _months(f.as_of_date, today)
    # The part of the balance nobody has seen leave a bank account.
    assumed = round(f.monthly_contribution * months if f.active else 0.0, 2)
    paid_in = round(f.opening_balance + assumed, 2)
    spent = round(sum(float(w.get("amount", 0)) for w in (f.withdrawals or [])), 2)
    balance = round(paid_in - spent, 2)
    return {
        "id": f.id,
        "name": f.name,
        "monthly_contribution": f.monthly_contribution,
        "opening_balance": f.opening_balance,
        "as_of_date": f.as_of_date.isoformat(),
        "target_annual": f.target_annual,
        "holder": f.holder,
        "withdrawals": f.withdrawals or [],
        "active": f.active,
        "notes": f.notes,
        # computed
        "months_elapsed": months,
        "paid_in": paid_in,        # opening + accrued monthly set-asides
        "spent": spent,            # drawn down so far
        "balance": balance,        # what the bucket SHOULD hold if every set-aside was transferred
        # How much of `balance` rests on set-asides being assumed to have happened. Equal to `balance`
        # for a fund opened at zero, and 0 for a fund that only ever held its stated opening balance.
        "assumed_inr": round(min(assumed, max(0.0, balance)), 2),
    }


def compute_sinking(db: Session, household_id: int | None = None) -> dict:
    """Fund list + computed balances + totals. Plain helper so internal callers (cockpit,
    networth, cashflow) can use it directly; the route wraps it with the current household.
    household_id=None = all."""
    today = date.today()
    q = db.query(SinkingFund)
    if household_id is not None:
        q = q.filter(SinkingFund.household_id == household_id)
    rows = [_view(f, today) for f in q.all()]
    return {
        "funds": rows,
        "total_balance": round(sum(r["balance"] for r in rows), 2),
        "monthly_set_aside": round(sum(r["monthly_contribution"] for r in rows if r["active"]), 2),
    }


@router.get("/sinking")
def list_sinking(db: Session = Depends(get_db),
                 household: Household = Depends(get_current_household)):
    return compute_sinking(db, household.id)


def _owned_fund(db: Session, fund_id: int, household: Household) -> SinkingFund:
    f = db.query(SinkingFund).filter(SinkingFund.id == fund_id,
                                     SinkingFund.household_id == household.id).first()
    if not f:
        raise HTTPException(404, "Fund not found")
    return f


@router.post("/sinking")
def create_sinking(body: SinkingIn, db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)):
    f = SinkingFund(household_id=household.id, **body.model_dump())
    db.add(f)
    db.commit()
    db.refresh(f)
    return _view(f, date.today())


@router.put("/sinking/{fund_id}")
def update_sinking(fund_id: int, body: SinkingIn, db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)):
    f = _owned_fund(db, fund_id, household)
    for k, v in body.model_dump().items():
        setattr(f, k, v)
    db.commit()
    db.refresh(f)
    return _view(f, date.today())


@router.post("/sinking/{fund_id}/withdraw")
def withdraw(fund_id: int, body: WithdrawIn, db: Session = Depends(get_db),
             household: Household = Depends(get_current_household)):
    f = _owned_fund(db, fund_id, household)
    if body.amount <= 0:
        raise HTTPException(400, "Withdrawal must be positive")
    log = list(f.withdrawals or [])
    log.append({"amount": body.amount, "date": body.date.isoformat(), "note": body.note or ""})
    log.sort(key=lambda w: w.get("date", ""))
    f.withdrawals = log
    db.commit()
    db.refresh(f)
    return _view(f, date.today())


@router.delete("/sinking/{fund_id}")
def delete_sinking(fund_id: int, db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)):
    f = _owned_fund(db, fund_id, household)
    db.delete(f)
    db.commit()
    return {"deleted": fund_id}
