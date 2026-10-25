"""Audit-trail endpoints: history, "what you correct most", and undo.

The audit log (models.audit.TransactionEvent) is the substrate for three things:
  • history  — every old→new change to a transaction, "why is this number what it is"
  • learning — what the household corrects most (edits_by_field, top recategorizations)
  • undo     — revert any recorded change, not just merges

Everything here is scoped to the current household, exactly like api/transactions.py: a household can
only ever read or undo events on its own ledger, never another's by id.
"""

from __future__ import annotations

from api.deps import get_current_household
from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from models.audit import TransactionEvent
from models.tenant import Household
from models.transaction import Transaction
from models.user import User
from services.audit import log_event
from sqlalchemy import func
from sqlalchemy.orm import Session

router = APIRouter()

# Edit-style actions whose old_value we know how to restore onto the transaction.
_REVERTIBLE = ("edit", "categorize", "undo")


def _field_value(t: Transaction, field: str | None):
    """Current value of `field` on the transaction (the 'account' alias reads the bank column)."""
    if field == "account":
        return t.bank
    return getattr(t, field, None)


@router.get("/audit/history")
def history(txn_id: int | None = None, limit: int = 100, db: Session = Depends(get_db),
            household: Household = Depends(get_current_household)):
    """The audit trail, newest first. With txn_id → that transaction's events; without → the
    household's recent events across all transactions."""
    q = db.query(TransactionEvent).filter(TransactionEvent.household_id == household.id)
    if txn_id is not None:
        q = q.filter(TransactionEvent.txn_id == txn_id)
    rows = q.order_by(TransactionEvent.created_at.desc()).limit(limit).all()

    # Names, resolved here rather than by the caller. `actor` says what KIND of thing acted and
    # `user_id` says which person, but "user 3 changed this" is not an answer anybody wanted — and
    # a screen that had to fetch the household's people to translate it would either duplicate this
    # join or, more likely, print the id.
    names = dict(db.query(User.id, User.name).filter(User.household_id == household.id).all())

    return [
        {
            "id": e.id,
            "txn_id": e.txn_id,
            "action": e.action,
            "field": e.field,
            "old_value": e.old_value,
            "new_value": e.new_value,
            "actor": e.actor,
            "user_id": e.user_id,
            # None for a scheduled sync, and for anything written before Artha recorded authorship.
            # The screen says "the nightly sync" or "someone" rather than inventing a person.
            "user_name": names.get(e.user_id),
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in rows
    ]


@router.get("/audit/corrections")
def corrections(db: Session = Depends(get_db),
                household: Household = Depends(get_current_household)):
    """"What you correct most" — surfaced from the household's own (actor='user') edits, the signal
    the categorizer should learn from. edits_by_field = how often each field gets corrected;
    top_recategorizations = the categories you most often move transactions INTO."""
    hh = TransactionEvent.household_id == household.id
    user = TransactionEvent.actor == "user"

    field_rows = (
        db.query(TransactionEvent.field, func.count(TransactionEvent.id))
        .filter(hh, user, TransactionEvent.action.in_(("edit", "undo")))
        .group_by(TransactionEvent.field)
        .all()
    )
    edits_by_field = {field: count for field, count in field_rows if field is not None}

    recat_rows = (
        db.query(TransactionEvent.new_value, func.count(TransactionEvent.id).label("n"))
        .filter(hh, user, TransactionEvent.action == "edit", TransactionEvent.field == "category")
        .group_by(TransactionEvent.new_value)
        .order_by(func.count(TransactionEvent.id).desc())
        .limit(10)
        .all()
    )
    top_recategorizations = [{"to": value, "count": count} for value, count in recat_rows]

    return {"edits_by_field": edits_by_field, "top_recategorizations": top_recategorizations}


@router.post("/audit/undo/{event_id}")
def undo(event_id: int, db: Session = Depends(get_db),
         household: Household = Depends(get_current_household)):
    """Revert the change recorded by one event, then log the reversal as a NEW undo event (the audit
    log is append-only — undoing is itself an event, so it too can be seen and undone)."""
    event = (db.query(TransactionEvent)
             .filter(TransactionEvent.id == event_id,
                     TransactionEvent.household_id == household.id).first())
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    t = (db.query(Transaction)
         .filter(Transaction.id == event.txn_id,
                 Transaction.household_id == household.id).first())

    if event.action == "delete":
        raise HTTPException(status_code=400, detail="Can't undo a permanent delete (the row is gone)")
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")

    before = _field_value(t, event.field)

    if event.action == "archive":
        t.archived = False
    elif event.action in _REVERTIBLE and event.field:
        field = event.field
        old = None if event.old_value == "None" else event.old_value
        if field == "amount":
            t.amount = float(old) if old is not None else None
        elif field == "archived":
            t.archived = (event.old_value == "True")
        elif field == "account":
            t.bank = old
        elif field == "date":
            t.date = old
            t.stmt_month = old[:7] if old else None
        elif field == "category":
            from services.categories import CATEGORY_TYPE
            t.category = old
            if t.direction == "debit":  # re-derive spend type so Plan/afford follow the revert
                t.txn_type = CATEGORY_TYPE.get(old, "SPEND")
        else:
            setattr(t, field, old)
    else:
        raise HTTPException(status_code=400, detail=f"Don't know how to undo a '{event.action}' event")

    reverted_to = _field_value(t, event.field)
    log_event(db, household_id=household.id, txn_id=t.id, action="undo",
              field=event.field, old=before, new=reverted_to, actor="user")
    db.commit()
    return {"ok": True, "restored": t.id, "field": event.field}
