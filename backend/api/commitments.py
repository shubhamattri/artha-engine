"""Commitment reconciliation — planned recurring outflows vs the actual debits that paid them."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_household
from database import get_db
from models.tenant import Household
from models.transaction import Transaction
from services import commitments, learned

router = APIRouter()


class LinkIn(BaseModel):
    txn_id: int
    commitment_ref: str   # "loan:3" | "cashflow:8" | "recurring:12" | "insurance:1"


class UnlinkIn(BaseModel):
    signature: str


@router.get("/commitments")
def get_commitments(month: str | None = None,
                    db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    """The recurring commitments (EMIs, SIPs, fixed expenses, insurance) that are LIVE in `month`
    (defaults to the current month) — a loan disbursed later or an expense not yet started is excluded."""
    month = month or date.today().strftime("%Y-%m")
    items = commitments.list_commitments(db, household.id, month)
    return {"commitments": items, "monthly_total": round(sum(c["amount"] for c in items), 2)}


@router.get("/commitments/candidates")
def commitment_candidates(ref: str, month: str | None = None,
                          db: Session = Depends(get_db),
                          household: Household = Depends(get_current_household)):
    """The debits this month that could pay a 'missing' commitment `ref` — the pick-list for the
    exception queue (which transfer is a household member's pocket money / a parent's support)."""
    month = month or date.today().strftime("%Y-%m")
    return commitments.candidates(db, household.id, ref, month)


@router.get("/commitments/reconcile")
def reconcile_commitments(month: str | None = None,
                          db: Session = Depends(get_db),
                          household: Household = Depends(get_current_household)):
    """Per-month: which commitments actually paid (✅), which are missing (⚠️), which debits are extra (➕).
    `month` = 'YYYY-MM' (defaults to the current month)."""
    month = month or date.today().strftime("%Y-%m")
    return commitments.reconcile(db, household.id, month)


@router.post("/commitments/link")
def link_commitment(body: LinkIn, db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    """Confirm that a transaction's payee IS a given commitment. Teaches the system that payee's
    identity so every future debit from them (any amount) auto-matches — no re-tagging."""
    t = db.query(Transaction).filter(Transaction.id == body.txn_id,
                                     Transaction.household_id == household.id).first()
    if not t:
        raise HTTPException(status_code=404, detail="transaction not found")
    sig = learned.signature(t.merchant or t.description or "")
    commitments.link(db, household.id, sig, body.commitment_ref, source="user")
    # Push the confirmed link onto categories NOW so the linked debit leaves variable spend immediately
    # (not only on the next sync) — the money stops double-counting against the budget the moment you link.
    recat = commitments.apply_link_categories(db, household.id)
    return {"ok": True, "signature": sig, "commitment_ref": body.commitment_ref,
            "recategorized": recat.get("recategorized", 0)}


@router.post("/commitments/unlink")
def unlink_commitment(body: UnlinkIn, db: Session = Depends(get_db),
                      household: Household = Depends(get_current_household)):
    """Undo a wrong link — forget this payee→commitment mapping. The commitment returns to 'missing'."""
    ok = commitments.unlink(db, household.id, body.signature)
    return {"ok": ok, "signature": body.signature}
