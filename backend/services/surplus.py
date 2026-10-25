"""Where a cycle's leftover went.

The leftover was computed each cycle, shown once, and then existed nowhere — the next cycle started
fresh and the money merged into the bank balance untracked. This makes it answerable.

A surplus is a CLAIM on money already held, never money itself. The rupees sit in a bank account
whose balance is already in net worth, so nothing here is added to net worth. Drawing on a surplus
to prepay a loan moves the same rupees from cash into debt reduction; net worth changes only by the
interest that avoids.
"""

from __future__ import annotations

from datetime import date, datetime

from models.surplus import SurplusAllocation, SurplusDraw
from sqlalchemy.orm import Session

PURPOSES = ("prepayment", "invest", "buffer", "unallocated")


def saved_in_cycle(db: Session, household_id: int, cycle_end: str, budget: dict | None = None) -> float:
    """What a cycle actually saved: the variable-spend pool it was given, minus what it spent.

    compute_budget only reports a leftover for the LIVE cycle — a closed one returns net_spent and
    nothing else. That is precisely why "we saved money last cycle, where did it go" had no answer:
    the figure existed while the cycle was open and vanished when it rolled.

    The pool comes from the CURRENT cash-flow plan, so a past cycle is measured against today's
    income and EMIs. If those have changed since, an older cycle's saving is approximate — stated
    here rather than presented as exact, because a number whose basis is invisible is how the
    gross-versus-net confusion started.
    """
    from api.budget import compute_budget
    from api.cashflow import compute_cashflow

    b = budget or compute_budget(db=db, household_id=household_id, anchor=cycle_end)
    pool = float(compute_cashflow(db, household_id).get("leftover") or 0.0)
    spent = float(b.get("net_spent_inr") or 0.0)
    return round(max(0.0, pool - spent), 2)


def unallocated_for_cycle(db: Session, household_id: int, cycle_end: str) -> float:
    """What is left of that cycle's surplus to earmark — its leftover minus what is already claimed.

    Re-derives the leftover from the budget rather than storing it, so a cycle whose transactions
    change later (a statement arrives, a duplicate is superseded) does not leave a stale figure
    behind. Today's session moved a closed cycle by Rs 25,000 twice over; a cached number would
    still be reporting the old one.
    """
    from api.budget import compute_budget

    b = compute_budget(db=db, household_id=household_id, anchor=cycle_end)
    leftover = float(saved_in_cycle(db, household_id, cycle_end, b))
    claimed = sum(a.amount for a in db.query(SurplusAllocation).filter(
        SurplusAllocation.household_id == household_id,
        SurplusAllocation.cycle_end == cycle_end).all())
    return round(leftover - claimed, 2)


def remaining(db: Session, allocation: SurplusAllocation) -> float:
    drawn = sum(x.amount for x in db.query(SurplusDraw).filter(
        SurplusDraw.allocation_id == allocation.id).all())
    return round(allocation.amount - drawn, 2)


def allocate(db: Session, household_id: int, cycle_end: str, amount: float,
             purpose: str, note: str | None = None) -> SurplusAllocation:
    """Earmark part of a cycle's leftover.

    Refuses to earmark more than the cycle actually left over. Without that the total claimed drifts
    above the money that exists and every figure downstream inherits the error silently.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES}")
    if amount <= 0:
        raise ValueError("amount must be positive")
    free = unallocated_for_cycle(db, household_id, cycle_end)
    if amount > free + 0.01:
        raise ValueError(
            f"that cycle has {free:,.2f} left to earmark, not {amount:,.2f} — "
            f"allocating more would claim money the cycle did not leave over")
    a = SurplusAllocation(household_id=household_id, cycle_end=cycle_end, amount=round(amount, 2),
                          purpose=purpose, note=note, created_at=datetime.utcnow())
    db.add(a)
    db.commit()
    return a


def draw(db: Session, allocation_id: int, amount: float, on: str | None = None,
         loan_id: int | None = None, transaction_id: int | None = None,
         note: str | None = None) -> SurplusDraw:
    """Spend against an earmark, recording what it went to.

    Refuses to overdraw. An allocation that can go negative stops being an answer to "what is left"
    and becomes another number to reconcile.
    """
    a = db.query(SurplusAllocation).filter(SurplusAllocation.id == allocation_id).first()
    if a is None:
        raise ValueError(f"no allocation {allocation_id}")
    if amount <= 0:
        raise ValueError("amount must be positive")
    left = remaining(db, a)
    if amount > left + 0.01:
        raise ValueError(f"only {left:,.2f} left on that allocation, cannot draw {amount:,.2f}")
    dr = SurplusDraw(allocation_id=allocation_id, amount=round(amount, 2), loan_id=loan_id,
                     transaction_id=transaction_id, note=note,
                     drawn_on=on or date.today().isoformat(), created_at=datetime.utcnow())
    db.add(dr)
    db.commit()
    return dr


def summary(db: Session, household_id: int) -> dict:
    """Every earmark, what is left on it, and what it has paid for."""
    allocs = (db.query(SurplusAllocation)
              .filter(SurplusAllocation.household_id == household_id)
              .order_by(SurplusAllocation.cycle_end.desc()).all())
    out, by_purpose = [], {}
    for a in allocs:
        draws = db.query(SurplusDraw).filter(SurplusDraw.allocation_id == a.id).all()
        left = remaining(db, a)
        by_purpose[a.purpose] = round(by_purpose.get(a.purpose, 0.0) + left, 2)
        out.append({
            "id": a.id, "cycle_end": a.cycle_end, "amount": a.amount, "purpose": a.purpose,
            "note": a.note, "remaining": left,
            "draws": [{"id": x.id, "amount": x.amount, "on": x.drawn_on, "loan_id": x.loan_id,
                       "transaction_id": x.transaction_id, "note": x.note} for x in draws],
        })
    return {
        "allocations": out,
        "total_remaining": round(sum(a["remaining"] for a in out), 2),
        "by_purpose": by_purpose,
        # Stated on every response so nobody has to remember it: this is a claim on money already
        # counted in net worth, not an additional asset.
        "note": "A claim on cash you already hold — already counted in net worth, never added to it.",
    }
