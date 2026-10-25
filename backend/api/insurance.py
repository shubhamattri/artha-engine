"""Insurance policies — coverage + (for ROP/endowment/ULIP) cash value.

  GET    /insurance         list + computed (premiums paid, maturity benefit) + totals
  POST   /insurance         add
  PUT    /insurance/{id}    edit
  DELETE /insurance/{id}
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_household
from database import get_db
from models.insurance import InsurancePolicy
from models.tenant import Household

router = APIRouter()


class PolicyIn(BaseModel):
    provider: str
    plan_name: str | None = None
    policy_no: str | None = None
    kind: str = "term"
    holder: str | None = None
    life_insured: str | None = None
    sum_assured: float = 0.0
    premium: float = 0.0
    premium_frequency: str = "monthly"
    start_date: date | None = None
    policy_term_years: int | None = None
    premium_paying_term_years: int | None = None
    return_of_premium: bool = False
    maturity_date: date | None = None
    surrender_value: float | None = None
    active: bool = True
    notes: str | None = None


def _months(start: date, end: date) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def _view(p: InsurancePolicy, today: date) -> dict:
    per_year = p.premium * (12 if p.premium_frequency == "monthly" else 1)
    months_paid = _months(p.start_date, today) if p.start_date else 0
    premiums_paid = round(p.premium * months_paid if p.premium_frequency == "monthly"
                          else per_year * (months_paid // 12), 2)
    # Return-of-Premium maturity benefit = total premiums over the paying term.
    ppt = p.premium_paying_term_years or p.policy_term_years or 0
    maturity_benefit = round(per_year * ppt, 2) if p.return_of_premium else 0.0
    return {
        "id": p.id, "provider": p.provider, "plan_name": p.plan_name, "policy_no": p.policy_no,
        "kind": p.kind, "holder": p.holder, "life_insured": p.life_insured,
        "sum_assured": p.sum_assured, "premium": p.premium, "premium_frequency": p.premium_frequency,
        "start_date": p.start_date.isoformat() if p.start_date else None,
        "policy_term_years": p.policy_term_years, "premium_paying_term_years": p.premium_paying_term_years,
        "return_of_premium": p.return_of_premium,
        "maturity_date": p.maturity_date.isoformat() if p.maturity_date else None,
        "surrender_value": p.surrender_value or 0.0, "active": p.active, "notes": p.notes,
        # computed
        "annual_premium": round(per_year, 2),
        "premiums_paid_so_far": premiums_paid,
        "maturity_benefit": maturity_benefit,   # money back at maturity (ROP)
    }


def compute_insurance(db: Session, household_id: int | None = None) -> dict:
    """Policy list + computed premiums/maturity + totals. Plain helper so internal callers
    (cashflow) can use it directly; the route wraps it with the current household.
    household_id=None = all."""
    today = date.today()
    q = db.query(InsurancePolicy)
    if household_id is not None:
        q = q.filter(InsurancePolicy.household_id == household_id)
    rows = [_view(p, today) for p in q.all()]
    return {
        "policies": rows,
        "total_cover": round(sum(r["sum_assured"] for r in rows if r["active"]), 2),
        "annual_premium": round(sum(r["annual_premium"] for r in rows if r["active"]), 2),
        # cash value that counts toward net worth (pure term = 0; ROP/endowment/ULIP = surrender value)
        "total_surrender_value": round(sum(r["surrender_value"] for r in rows), 2),
    }


@router.get("/insurance")
def list_policies(db: Session = Depends(get_db),
                  household: Household = Depends(get_current_household)):
    return compute_insurance(db, household.id)


def _owned_policy(db: Session, pid: int, household: Household) -> InsurancePolicy:
    p = db.query(InsurancePolicy).filter(InsurancePolicy.id == pid,
                                         InsurancePolicy.household_id == household.id).first()
    if not p:
        raise HTTPException(404, "Policy not found")
    return p


@router.post("/insurance")
def create_policy(body: PolicyIn, db: Session = Depends(get_db),
                  household: Household = Depends(get_current_household)):
    p = InsurancePolicy(household_id=household.id, **body.model_dump())
    db.add(p); db.commit(); db.refresh(p)
    return _view(p, date.today())


@router.put("/insurance/{pid}")
def update_policy(pid: int, body: PolicyIn, db: Session = Depends(get_db),
                  household: Household = Depends(get_current_household)):
    p = _owned_policy(db, pid, household)
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    db.commit(); db.refresh(p)
    return _view(p, date.today())


@router.delete("/insurance/{pid}")
def delete_policy(pid: int, db: Session = Depends(get_db),
                  household: Household = Depends(get_current_household)):
    p = _owned_policy(db, pid, household)
    db.delete(p); db.commit()
    return {"deleted": pid}
