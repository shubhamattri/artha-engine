"""The household wallet: what past cycles saved, and spending it from anywhere."""

from api.deps import get_current_household, get_db
from fastapi import APIRouter, Depends, HTTPException
from models.tenant import Household
from pydantic import BaseModel
from services import wallet
from sqlalchemy.orm import Session

router = APIRouter()


class SpendIn(BaseModel):
    amount: float
    on: str | None = None
    loan_id: int | None = None
    transaction_id: int | None = None
    note: str | None = None


@router.get("/wallet")
def get_wallet(db: Session = Depends(get_db),
               household: Household = Depends(get_current_household)):
    """Balance, what each closed cycle put in, and everything taken out with what it paid for."""
    return wallet.summary(db, household.id)


@router.post("/wallet/spend")
def post_spend(body: SpendIn, db: Session = Depends(get_db),
               household: Household = Depends(get_current_household)):
    try:
        d = wallet.spend(db, household.id, body.amount, body.on, body.loan_id,
                         body.transaction_id, body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"id": d.id, "balance": wallet.balance(db, household.id)}
